"""The copilot's reasoning core: retrieval tools over the live corpus, an LLM
tool-calling loop, and a deterministic engine that answers when no model is reachable.

Why this exists
---------------
The copilot used to see the corpus through one twenty-line aggregate briefing plus a
substring search that returned six rows per table. Asked about a specific FIR it knew
the crime type and status and nothing else; asked about a person it knew a name and a
risk band; asked a follow-up ("who else was in that case?") it had nothing to look at.
It answered fluently anyway, which is worse than not answering.

This module gives it the same view of a record an investigator has:

* **Tools** that read the full record — a case with its narrative, accused, victim,
  complainant, IO, arrests, chargesheet, court stage, linked vehicles, cyber
  identifiers, CCTV and Dial-112 calls; a person with every case they are named in,
  co-offenders, syndicates, risk drivers and court outcomes; officers; registries;
  filtered counts and breakdowns; paths through the link graph; every ML output; and
  the platform's own documentation (`copilot_knowledge`).
* **An LLM loop** (OpenAI-compatible tool calling) that decides which tools to call,
  reads the results, and answers. Entities the question names are resolved and
  pre-fetched before the first call, so the common case costs one round trip.
* **A deterministic engine** that resolves the same entities and composes an answer
  from the same tools when no provider is configured or all of them fail. The demo
  must not depend on a third-party API being up.

Rules it holds to
-----------------
* Every tool result sent to a provider has person names replaced with request-scoped
  aliases (`privacy.mask`) and contact numbers dropped; answers are restored locally.
* Tools return what is recorded and say what is missing. They never fill a gap.
* Ranked lists of people (watchlists) are returned as identifiers and scores, never
  names — the platform's stated position (agent_answers._watchlist).
"""
import json
import logging
import os
import re
import time

import pandas as pd

import copilot_knowledge
import privacy

LOG = logging.getLogger("ksp.copilot")

MAX_TOOL_STEPS = int(os.environ.get("COPILOT_MAX_TOOL_STEPS", "5"))
LLM_TIMEOUT_S = float(os.environ.get("COPILOT_LLM_TIMEOUT", "25"))

_GRAPH_GETTER = None


def configure(graph_getter):
    """main.py rebinds its knowledge graph on reload, so read it through a getter."""
    global _GRAPH_GETTER
    _GRAPH_GETTER = graph_getter


def _graph():
    try:
        return _GRAPH_GETTER() if _GRAPH_GETTER else None
    except Exception:
        return None


# =============================================================================
# Small helpers
# =============================================================================
def _frame(cache, key):
    df = cache.get(key)
    return df if df is not None and hasattr(df, "empty") and not df.empty else None


def _clean(v):
    """JSON-safe scalar; NaN/None/'nan' become None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            pass
    if isinstance(v, str) and v.strip().lower() in ("", "nan", "none", "null"):
        return None
    return v


def _truthy(v):
    v = _clean(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y")
    return bool(v)


def _rec(row, cols):
    return {c: _clean(row.get(c)) for c in cols if c in row}


def _fmt_int(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _date(v):
    v = _clean(v)
    return str(v)[:10] if v else None


# =============================================================================
# Corpus index — built once per loaded corpus
# =============================================================================
class _Index:
    pass


def _index(cache):
    firs = _frame(cache, "firs")
    if firs is None:
        return None
    key = (id(firs), len(firs))
    idx = cache.get("_copilot_index")
    if idx is not None and getattr(idx, "key", None) == key:
        return idx

    t0 = time.time()
    idx = _Index()
    idx.key = key
    f = firs.copy()
    f["case_id"] = f["case_id"].astype(str)
    f["_ts"] = pd.to_datetime(f["timestamp"], errors="coerce")
    idx.firs = f
    idx.case_pos = {cid: i for i, cid in enumerate(f["case_id"].tolist())}
    idx.cm2case = {}
    if "CaseMasterID" in f:
        idx.cm2case = {int(cm): cid for cm, cid in zip(f["CaseMasterID"], f["case_id"]) if pd.notna(cm)}
    idx.case2cm = {v: k for k, v in idx.cm2case.items()}
    idx.caseno2case = {}
    if "CaseNo" in f:
        for cno, cid in zip(f["CaseNo"].astype(str), f["case_id"]):
            idx.caseno2case.setdefault(cno, []).append(cid)

    links = _frame(cache, "links")
    idx.links_by_case, idx.links_by_person = {}, {}
    if links is not None:
        for cid, sid, rel in zip(links["case_id"].astype(str), links["suspect_id"].astype(str),
                                 links["relationship"].astype(str)):
            idx.links_by_case.setdefault(cid, []).append((sid, rel))
            idx.links_by_person.setdefault(sid, []).append((cid, rel))

    crim = _frame(cache, "criminals")
    idx.criminals = {}
    if crim is not None:
        for r in crim.to_dict("records"):
            idx.criminals[str(r["criminal_id"])] = r
    susp = _frame(cache, "suspects")
    if susp is not None:
        for r in susp.to_dict("records"):
            idx.criminals.setdefault(str(r["suspect_id"]), r)

    acc = _frame(cache, "Accused")
    idx.accused_age = {}
    if acc is not None:
        for pid, age in zip(acc["PersonID"].astype(str), acc["AgeYear"]):
            if pd.notna(age):
                idx.accused_age.setdefault(pid, int(age))

    rep = _frame(cache, "reports")
    idx.reports = dict(zip(rep["case_id"].astype(str), rep["report_text"])) if rep is not None else {}

    courts = _frame(cache, "courts")
    idx.court = {}
    if courts is not None:
        for r in courts.to_dict("records"):
            idx.court[str(r.get("fir_number"))] = r

    cs = _frame(cache, "ChargesheetDetails")
    idx.chargesheet = {}
    if cs is not None:
        for r in cs.to_dict("records"):
            idx.chargesheet.setdefault(int(r["CaseMasterID"]), r)

    ar = _frame(cache, "ArrestSurrender")
    idx.arrests = {}
    if ar is not None:
        for r in ar.to_dict("records"):
            idx.arrests.setdefault(int(r["CaseMasterID"]), []).append(r)

    comp = _frame(cache, "ComplainantDetails")
    idx.complainant = {}
    if comp is not None:
        for r in comp.to_dict("records"):
            idx.complainant.setdefault(int(r["CaseMasterID"]), r)

    off = _frame(cache, "officers")
    idx.officers = {str(r["officer_id"]): r for r in off.to_dict("records")} if off is not None else {}

    def _by(key_df, col):
        out = {}
        df = _frame(cache, key_df)
        if df is not None and col in df:
            for r in df.to_dict("records"):
                k = _clean(r.get(col))
                if k:
                    out.setdefault(str(k), []).append(r)
        return out

    idx.vehicles_by_case = _by("vehicles", "linked_case_id")
    idx.cyber_by_case = _by("cybercrime", "case_id")
    idx.cctv_by_case = _by("cctv", "case_link")
    idx.calls_by_case = _by("complaints", "linked_case_id")

    pend = (cache.get("pendency") or {}).get("cases") or []
    idx.pendency = {str(c.get("case_id")): c for c in pend if isinstance(c, dict)}
    idx.anomalies = {str(a.get("case_id")): a for a in (cache.get("anomalies") or []) if isinstance(a, dict)}

    # Person-name index: lowercase full name -> [(kind, id)].
    names = {}

    def _add(name, kind, ident):
        n = _clean(name)
        if not n:
            return
        k = re.sub(r"\s+", " ", str(n).strip().lower())
        if len(k) >= 4:
            names.setdefault(k, []).append((kind, str(ident)))

    for sid, r in idx.criminals.items():
        _add(r.get("name"), "offender", sid)
    for oid, r in idx.officers.items():
        _add(r.get("name"), "officer", oid)
    miss = _frame(cache, "missing")
    if miss is not None:
        for r in miss.to_dict("records"):
            _add(r.get("name"), "missing", r.get("person_id"))
    idx.names = names
    # Victims are indexed separately: thousands of them share common names, and a
    # victim match should never outrank an offender or officer of the same name.
    idx.victim_names = {}
    for cid, vn in zip(f["case_id"], f.get("victim_name", pd.Series([None] * len(f)))):
        n = _clean(vn)
        if n:
            idx.victim_names.setdefault(str(n).strip().lower(), []).append(cid)

    rec = cache.get("recidivism") or {}
    idx.rec_scores = rec.get("scores") or {}
    idx.rec_prob = rec.get("probabilities") or {}
    idx.rec_top = {str(t.get("criminal_id")): t for t in (rec.get("top") or [])}

    idx.syndicate_of = {}
    for syn in cache.get("syndicate_communities") or []:
        for m in syn.get("members") or []:
            idx.syndicate_of.setdefault(str(m.get("id")), []).append(syn)

    idx.districts = sorted(f["district"].dropna().astype(str).unique().tolist())
    idx.crime_types = sorted(f["crime_type"].dropna().astype(str).unique().tolist())
    idx.stations = sorted(f["police_station"].dropna().astype(str).unique().tolist()) if "police_station" in f else []

    try:
        cache["_copilot_index"] = idx
    except Exception:
        pass
    LOG.info("copilot index built in %.2fs (%d names)", time.time() - t0, len(names))
    return idx


# =============================================================================
# Entity resolution
# =============================================================================
RE_CRIMENO = re.compile(r"\b(\d{15,20})\b")
RE_FIRSHORT = re.compile(r"\b(?:fir|case|crime)\s*(?:no\.?|number|#)?\s*[-_:]?\s*0*(\d{4,9})\b", re.I)
RE_SUS = re.compile(r"\b(?:sus|suspect|offender|accused)\s*[-_#]?\s*0*(\d{3,6})\b", re.I)
RE_KGID = re.compile(r"\bKGID\s*-?\s*(\d{4,8})\b", re.I)
RE_MP = re.compile(r"\bMP\s*-\s*(\d{3,6})\b", re.I)
RE_VEH = re.compile(r"\b(KA)\s*-?\s*(\d{1,2})\s*-?\s*([A-Z]{1,3})\s*-?\s*(\d{3,4})\b", re.I)
RE_PHONE = re.compile(r"\b([6-9]\d{9})\b")
RE_UPI = re.compile(r"\b([\w.\-]{2,}@[a-z]{2,})\b", re.I)
RE_YEAR = re.compile(r"\b(20[12]\d)\b")

CRIME_SYNONYMS = {
    "Chain Snatching": ["chain snatch", "snatching", "chain theft", "mangalsutra", "gold chain"],
    "Extortion": ["extortion", "hafta", "protection money", "blackmail", "extort", "ransom"],
    "Narcotics Distribution": ["narcotic", "drug", "ndps", "peddl", "ganja", "mdma", "cocaine"],
    "Vehicle Theft": ["vehicle theft", "car theft", "bike theft", "motorcycle theft", "auto theft",
                      "vehicle lifting", "two-wheeler theft"],
    "House Break-in (Night)": ["house break", "housebreak", "burglary", "burglar", "break-in",
                               "break in", "home invasion", "dacoity"],
    "Sandalwood Smuggling": ["sandalwood", "sandal wood", "red sanders", "timber smuggl", "smuggling"],
    "Cyber Fraud (Jamtara style)": ["cyber fraud", "cybercrime", "cyber crime", "online fraud",
                                    "phishing", "jamtara", "upi fraud", "otp fraud", "digital arrest",
                                    "online scam", "cyber"],
    "Murder": ["murder", "murders", "killing", "killed"],
    "Homicide": ["homicide", "homicides", "culpable"],
    "Assault": ["assault", "assaults", "attack", "beating", "hurt", "grievous"],
}

STATUS_SYNONYMS = {
    "Under Investigation": ["under investigation", "pending investigation", "open case", "open cases",
                            "unsolved", "pending fir", "pending firs", "investigating", "still open"],
    "Charge Sheeted": ["charge sheeted", "chargesheeted", "charge-sheeted", "chargesheet filed"],
    "Closed": ["closed", "disposed"],
}

PRONOUN_CASE = re.compile(r"\b(this|that|the same|same|current|above|previous|last)\s+(case|fir|crime|incident|one)\b|\b(it|its)\b", re.I)
PRONOUN_PERSON = re.compile(r"\b(he|she|him|her|his|hers|they|them|their|this (person|suspect|offender|accused|guy|man|woman)|that (person|suspect|offender|accused|guy|man|woman))\b", re.I)


def _norm_key(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def detect_district(text, idx):
    low = (text or "").lower()
    best = None
    for d in idx.districts:
        dl = d.lower()
        if re.search(r"(?<![a-z])" + re.escape(dl) + r"(?![a-z])", low) and (best is None or len(d) > len(best)):
            best = d
    if best:
        return best
    aliases = {"bangalore": "Bengaluru Urban", "bengaluru": "Bengaluru Urban", "mysore": "Mysuru",
               "mangalore": "Dakshina Kannada", "mangaluru": "Dakshina Kannada", "hubli": "Dharwad",
               "hubballi": "Dharwad", "belgaum": "Belagavi", "gulbarga": "Kalaburagi",
               "bellary": "Ballari", "shimoga": "Shivamogga", "tumkur": "Tumakuru",
               "bijapur": "Vijayapura", "chikmagalur": "Chikkamagaluru", "hospet": "Vijayanagara",
               "hosapete": "Vijayanagara", "coorg": "Kodagu", "karwar": "Uttara Kannada",
               "udupi": "Udupi"}
    for a, d in aliases.items():
        if re.search(r"(?<![a-z])" + a + r"(?![a-z])", low) and d in idx.districts:
            return d
    return None


def detect_crime(text, idx):
    low = (text or "").lower()
    best, best_len = None, 0
    for ct in idx.crime_types:
        head = ct.split("(")[0].strip().lower()
        if head and head in low and len(head) > best_len:
            best, best_len = ct, len(head)
    for ct, syns in CRIME_SYNONYMS.items():
        if ct not in idx.crime_types:
            continue
        for s in syns:
            if re.search(r"(?<![a-z])" + re.escape(s), low) and len(s) > best_len:
                best, best_len = ct, len(s)
    return best


def detect_status(text):
    low = (text or "").lower()
    for st, syns in STATUS_SYNONYMS.items():
        if any(s in low for s in syns):
            return st
    return None


def detect_station(text, idx):
    low = (text or "").lower()
    best = None
    for s in idx.stations:
        base = s.lower().replace(" ps", "").strip()
        if len(base) >= 4 and re.search(r"(?<![a-z])" + re.escape(base) + r"(?![a-z])", low):
            if best is None or len(s) > len(best):
                best = s
    return best


# Enough Kannada for the offline engine to read the common question shapes; the LLM
# path understands Kannada natively.
KANNADA_TERMS = {
    "ಕೊಲೆ": "murder", "ಹಲ್ಲೆ": "assault", "ಸರಗಳ್ಳತನ": "chain snatching", "ಕಳ್ಳತನ": "theft",
    "ವಾಹನ ಕಳ್ಳತನ": "vehicle theft", "ಮನೆಗಳ್ಳತನ": "house break", "ಸುಲಿಗೆ": "extortion",
    "ಮಾದಕ": "narcotics", "ಗಾಂಜಾ": "ganja", "ಶ್ರೀಗಂಧ": "sandalwood", "ಸೈಬರ್": "cyber fraud",
    "ವಂಚನೆ": "fraud", "ಎಷ್ಟು": "how many", "ಪ್ರಕರಣ": "cases", "ಜಿಲ್ಲೆ": "district",
    "ಠಾಣೆ": "station", "ತನಿಖೆಯಲ್ಲಿ": "under investigation", "ರೌಡಿ": "rowdy", "ವಾರಂಟ್": "warrant",
    "ಮುನ್ಸೂಚನೆ": "forecast", "ಹೆಚ್ಚು": "most", "ಆರೋಪಿ": "accused",
    "ಬೆಂಗಳೂರು": "bengaluru", "ಮೈಸೂರ": "mysuru", "ಬೆಳಗಾವಿ": "belagavi", "ಕಲಬುರಗಿ": "kalaburagi",
    "ಮಂಗಳೂರು": "mangaluru", "ಹುಬ್ಬಳ್ಳಿ": "hubballi", "ತುಮಕೂರು": "tumakuru", "ಬಳ್ಳಾರಿ": "ballari",
    "ಶಿವಮೊಗ್ಗ": "shivamogga", "ಹಾಸನ": "hassan", "ಮಂಡ್ಯ": "mandya", "ಉಡುಪಿ": "udupi",
}


def _kannada_to_english(text):
    if not re.search(r"[\u0C80-\u0CFF]", text or ""):
        return text
    out = text
    for kn, en in sorted(KANNADA_TERMS.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(kn, f" {en} ")
    return out


def resolve_case(ref, idx):
    """Any case reference -> canonical case_id, or None."""
    if ref is None:
        return None
    s = str(ref).strip()
    m = RE_CRIMENO.search(s)
    if m and m.group(1) in idx.case_pos:
        return m.group(1)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits in idx.case_pos:
        return digits
    try:
        n = int(digits)
    except ValueError:
        return None
    if n in idx.cm2case:
        return idx.cm2case[n]
    if digits in idx.caseno2case:
        return idx.caseno2case[digits][0]
    return None


def resolve_suspect(ref, idx):
    if ref is None:
        return None
    s = str(ref).strip().upper()
    m = re.search(r"(\d{3,6})", s)
    if not m:
        return None
    cand = f"SUS-{int(m.group(1))}"
    if cand in idx.criminals:
        return cand
    cand2 = f"SUS-{m.group(1)}"
    return cand2 if cand2 in idx.criminals else None


def resolve_officer(ref, idx):
    if ref is None:
        return None
    m = re.search(r"(\d{4,8})", str(ref))
    if not m:
        return None
    oid = f"KGID{m.group(1)}"
    return oid if oid in idx.officers else None


def find_names(text, idx):
    """Person names appearing in free text, longest first: [(name, [(kind,id)...])]."""
    words = re.findall(r"[A-Za-z][A-Za-z.'-]+", text or "")
    found, used = [], set()
    for n in (3, 2):
        for i in range(len(words) - n + 1):
            if any(j in used for j in range(i, i + n)):
                continue
            key = _norm_key(" ".join(words[i:i + n]))
            if key in idx.names:
                found.append((" ".join(words[i:i + n]), idx.names[key]))
                used.update(range(i, i + n))
    return found


def extract_entities(text, idx, context=None, history=None):
    """Everything identifiable in a question, plus pronoun resolution."""
    e = {"cases": [], "suspects": [], "officers": [], "missing": [], "vehicles": [],
         "identifiers": [], "names": []}
    t = text or ""
    for m in RE_CRIMENO.finditer(t):
        c = resolve_case(m.group(1), idx)
        if c and c not in e["cases"]:
            e["cases"].append(c)
    for m in RE_FIRSHORT.finditer(t):
        c = resolve_case(m.group(1), idx)
        if c and c not in e["cases"]:
            e["cases"].append(c)
    for m in re.finditer(r"\bSUS\s*-?\s*(\d{3,6})\b", t, re.I):
        s = resolve_suspect(m.group(1), idx)
        if s and s not in e["suspects"]:
            e["suspects"].append(s)
    for m in RE_SUS.finditer(t):
        s = resolve_suspect(m.group(1), idx)
        if s and s not in e["suspects"]:
            e["suspects"].append(s)
    for m in RE_KGID.finditer(t):
        o = resolve_officer(m.group(1), idx)
        if o and o not in e["officers"]:
            e["officers"].append(o)
    for m in RE_MP.finditer(t):
        e["missing"].append(f"MP-{m.group(1)}")
    for m in RE_VEH.finditer(t):
        e["vehicles"].append(f"{m.group(1).upper()}-{int(m.group(2)):02d}-{m.group(3).upper()}-{m.group(4)}")
    for m in RE_PHONE.finditer(t):
        if not RE_CRIMENO.search(m.group(0)):
            e["identifiers"].append(m.group(1))
    for m in RE_UPI.finditer(t):
        e["identifiers"].append(m.group(1).lower())
    for name, hits in find_names(t, idx):
        e["names"].append({"name": name, "matches": hits})
        for kind, ident in hits:
            if kind == "offender" and len(hits) == 1 and ident not in e["suspects"]:
                e["suspects"].append(ident)
            if kind == "officer" and len(hits) == 1 and ident not in e["officers"]:
                e["officers"].append(ident)

    # "the first one", "the second accused" -> that position in the previous answer's list.
    om = re.search(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|last)\s+(one|person|accused|suspect|offender|co-?offender|guy|man|woman)?\b", t, re.I)
    if om and not e["suspects"]:
        prev = next((str(h.get("content") or "") for h in reversed(history or []) if h.get("role") == "assistant"), "")
        ids = list(dict.fromkeys(m.upper() for m in re.findall(r"SUS-\d{3,6}", prev, re.I)))
        pos = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3,
               "fifth": 4, "5th": 4, "last": -1}[om.group(1).lower()]
        if ids and (pos == -1 or pos < len(ids)):
            e["suspects"].append(ids[pos])
            e["resolved_from_context"] = ids[pos]

    t = _kannada_to_english(t)
    e["district"] = detect_district(t, idx)
    e["crime_type"] = detect_crime(t, idx)
    e["status"] = detect_status(t)
    e["station"] = detect_station(t, idx)
    ym = RE_YEAR.search(t)
    e["year"] = int(ym.group(1)) if ym else None

    # Pronouns -> the entity in focus: the page being viewed, else the latest one
    # named in the conversation.
    e["resolved_from_context"] = None
    pm = PRONOUN_CASE.search(t)
    if pm and pm.group(0).lower() in ("it", "its") and (e["district"] or e["crime_type"]):
        pm = None      # "is it rising in Mysuru" is about Mysuru, not the case on screen
    if not e["cases"] and pm:
        c = (context or {}).get("case_id") or _last_in_history(history, "case", idx)
        c = resolve_case(c, idx) if c else None
        if c:
            e["cases"].append(c)
            e["resolved_from_context"] = c
    if not e["suspects"] and not e["names"] and PRONOUN_PERSON.search(t):
        s = (context or {}).get("suspect_id") or _last_in_history(history, "suspect", idx)
        s = resolve_suspect(s, idx) if s else None
        if s:
            e["suspects"].append(s)
            e["resolved_from_context"] = s
    return e


def _last_in_history(history, kind, idx):
    """The subject of the most recent turn that named one.

    The *first* id in a message, not the last: an answer leads with the record it is
    about and then lists related ones, so "does he have a warrant?" after a profile
    means the person profiled, not the fifth co-offender listed under them.
    """
    for h in reversed(history or []):
        content = str(h.get("content") or "")
        if kind == "case":
            for m in RE_CRIMENO.findall(content):
                if m in idx.case_pos:
                    return m
            for m in RE_FIRSHORT.findall(content):
                c = resolve_case(m, idx)
                if c:
                    return c
        else:
            ms = re.findall(r"SUS-\d{3,6}", content, re.I)
            if ms:
                return ms[0].upper()
    return None


# =============================================================================
# Tools
# =============================================================================
CASE_COLS = ["crime_type", "district", "police_station", "status", "severity_score", "time_of_day",
             "target_type", "weapon_used", "vehicle_used", "ipc_bns_sections", "fatalities",
             "injured", "witnesses", "accused_count"]


def _person_brief(sid, idx, rel=None):
    r = idx.criminals.get(sid, {})
    out = {"suspect_id": sid, "name": _clean(r.get("name")), "relationship": rel,
           "alias": _clean(r.get("alias")), "age": idx.accused_age.get(sid),
           "gang": _clean(r.get("gang_affiliation")), "risk_level": _clean(r.get("risk_level")),
           "prior_firs": _clean(r.get("past_fir_count")),
           "rowdy_sheeter": _truthy(r.get("rowdy_sheeter")),
           "history_sheeter": _truthy(r.get("history_sheet")),
           "active_warrant": _truthy(r.get("active_warrant"))}
    if sid in idx.rec_scores:
        out["recidivism_index"] = idx.rec_scores[sid]
    return {k: v for k, v in out.items() if v not in (None, "")}


def tool_get_case(cache, case_ref=None, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    cid = resolve_case(case_ref, idx)
    if not cid:
        return {"error": f"No case matching '{case_ref}' exists in the corpus.",
                "hint": "Case ids are 18-digit CrimeNo values; short forms like FIR-20345 use CaseMasterID."}
    row = idx.firs.iloc[idx.case_pos[cid]].to_dict()
    cm = idx.case2cm.get(cid)
    out = {"case_id": cid, "case_master_id": cm, "case_no": _clean(row.get("CaseNo")),
           "registered": _clean(row.get("timestamp"))}
    out.update(_rec(row, CASE_COLS))
    out["location"] = {"lat": _clean(row.get("latitude")), "lng": _clean(row.get("longitude"))}
    narrative = idx.reports.get(cid)
    if narrative:
        out["narrative"] = str(narrative)[:1100]
    out["victim"] = {k: v for k, v in {
        "name": _clean(row.get("victim_name")), "age": _clean(row.get("victim_age")),
        "gender": _clean(row.get("victim_gender")),
        "is_police_personnel": _truthy(row.get("victim_police"))}.items() if v is not None}
    comp = idx.complainant.get(cm) if cm is not None else None
    if comp:
        out["complainant"] = {"name": _clean(comp.get("ComplainantName")), "age": _clean(comp.get("AgeYear"))}
    out["accused"] = [_person_brief(sid, idx, rel) for sid, rel in idx.links_by_case.get(cid, [])]
    oid = _clean(row.get("officer_id"))
    if oid and oid in idx.officers:
        o = idx.officers[oid]
        out["investigating_officer"] = {"officer_id": oid, "name": _clean(o.get("name")),
                                        "rank": _clean(o.get("rank")), "station": _clean(o.get("station"))}
    elif oid:
        out["investigating_officer"] = {"officer_id": oid}
    arrests = idx.arrests.get(cm, []) if cm is not None else []
    out["arrests"] = {"count": len(arrests),
                      "first_date": min((_date(a.get("ArrestSurrenderDate")) for a in arrests
                                         if _date(a.get("ArrestSurrenderDate"))), default=None)}
    cs = idx.chargesheet.get(cm) if cm is not None else None
    if cs:
        out["chargesheet"] = {"filed_on": _date(cs.get("csdate")), "type": _clean(cs.get("cstype"))}
    court = idx.court.get(cid)
    if court:
        out["court"] = _rec(court, ["court_case_no", "court_name", "chargesheet_status",
                                    "conviction_status", "next_hearing"])
    veh = idx.vehicles_by_case.get(cid)
    if veh:
        out["stolen_vehicles"] = [_rec(v, ["vehicle_number", "make_model", "theft_date",
                                           "recovery_status", "theft_district"]) for v in veh[:3]]
    cy = idx.cyber_by_case.get(cid)
    if cy:
        c = cy[0]
        out["cyber_identifiers"] = _rec(c, ["upi_ids", "bank_accounts", "phone_numbers", "ip_addresses",
                                            "urls", "crypto_wallet", "fraud_amount", "victim_count"])
        rings = [r for r in ((cache.get("extended") or {}).get("cyber_rings") or []) if cid in (r.get("cases") or [])]
        if rings:
            out["cyber_rings"] = [{"identifier": r["identifier"], "linked_cases": r["linked_cases"],
                                   "total_fraud": r["total_fraud"]} for r in rings[:3]]
    cams = idx.cctv_by_case.get(cid)
    if cams:
        out["cctv"] = [_rec(c, ["camera_id", "timestamp", "detected_vehicle", "detected_faces"]) for c in cams[:3]]
    calls = idx.calls_by_case.get(cid)
    if calls:
        out["dial112_calls"] = [_rec(c, ["complaint_id", "emergency_type", "response_time_min",
                                         "resolution_status"]) for c in calls[:3]]
    p = idx.pendency.get(cid)
    if p:
        out["pendency_model"] = {"days_open": p.get("days_open"),
                                 "expected_additional_days": p.get("expected_additional_days"),
                                 "breach_risk": p.get("breach_risk")}
    if cid in idx.anomalies:
        out["anomaly_flag"] = idx.anomalies[cid].get("reasons")
    out["similar_cases"] = _similar_cases(row, cid, idx)
    return out


def _similar_cases(row, cid, idx, k=3):
    f = idx.firs
    pool = f[(f["crime_type"] == row.get("crime_type")) & (f["case_id"] != cid)]
    if pool.empty:
        return []
    score = ((pool["district"] == row.get("district")).astype(int) * 3
             + (pool["weapon_used"] == row.get("weapon_used")).astype(int) * 2
             + (pool["vehicle_used"] == row.get("vehicle_used")).astype(int) * 2
             + (pool["time_of_day"] == row.get("time_of_day")).astype(int)
             + (pool["target_type"] == row.get("target_type")).astype(int))
    top = pool.assign(_s=score).sort_values(["_s", "severity_score"], ascending=False).head(k)
    out = []
    for _, r in top.iterrows():
        shared = [lab for lab, col in (("district", "district"), ("weapon", "weapon_used"),
                                       ("vehicle", "vehicle_used"), ("time band", "time_of_day"),
                                       ("target", "target_type")) if r[col] == row.get(col)]
        out.append({"case_id": r["case_id"], "district": r["district"], "status": r["status"],
                    "registered": _date(r["timestamp"]), "shared_mo": shared})
    return out


def tool_get_person(cache, person_ref=None, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    ref = str(person_ref or "").strip()
    sid = resolve_suspect(ref, idx) if re.search(r"\d", ref) else None
    if not sid:
        hits = idx.names.get(_norm_key(ref), [])
        offenders = [i for k, i in hits if k == "offender"]
        others = [{"kind": k, "id": i} for k, i in hits if k != "offender"]
        victim_cases = idx.victim_names.get(_norm_key(ref), [])
        if len(offenders) == 1:
            sid = offenders[0]
        elif len(offenders) > 1:
            return {"ambiguous": True, "query": ref,
                    "candidates": [_person_brief(s, idx) for s in offenders[:8]],
                    "note": f"{len(offenders)} offenders share this name; ask which id."}
        else:
            if others or victim_cases:
                return {"query": ref, "offender_record": None, "other_matches": others,
                        "named_as_victim_in": victim_cases[:8],
                        "victim_case_count": len(victim_cases)}
            return {"error": f"No person named or numbered '{ref}' is in the registry."}
    r = idx.criminals.get(sid, {})
    prof = _person_brief(sid, idx)
    prof.update({"fingerprint_id": _clean(r.get("fingerprint_id")),
                 "past_arrests": _clean(r.get("past_arrests")),
                 "known_associates_on_file": [a for a in str(_clean(r.get("known_associates")) or "").split(";") if a]})
    cases = []
    for cid, rel in idx.links_by_person.get(sid, []):
        pos = idx.case_pos.get(cid)
        if pos is None:
            continue
        c = idx.firs.iloc[pos]
        court = idx.court.get(cid) or {}
        cases.append({"case_id": cid, "role": rel, "crime_type": c["crime_type"], "district": c["district"],
                      "station": c.get("police_station"), "registered": _date(c["timestamp"]),
                      "status": c["status"], "severity": _clean(c["severity_score"]),
                      "court_outcome": _clean(court.get("conviction_status"))})
    cases.sort(key=lambda x: x["registered"] or "", reverse=True)
    prof["cases_in_corpus"] = len(cases)
    if cases:
        cdf = pd.DataFrame(cases)
        prof["crime_types"] = cdf["crime_type"].value_counts().to_dict()
        prof["districts"] = cdf["district"].value_counts().head(6).to_dict()
        prof["case_status"] = cdf["status"].value_counts().to_dict()
        prof["court_outcomes"] = cdf["court_outcome"].dropna().value_counts().to_dict()
        prof["first_seen"] = cases[-1]["registered"]
        prof["last_seen"] = cases[0]["registered"]
    prof["cases"] = cases[:15]
    co = {}
    for cid, _rel in idx.links_by_person.get(sid, []):
        for other, _r2 in idx.links_by_case.get(cid, []):
            if other != sid:
                co[other] = co.get(other, 0) + 1
    prof["co_offenders"] = [dict(_person_brief(o, idx), shared_cases=n)
                            for o, n in sorted(co.items(), key=lambda x: -x[1])[:8]]
    t = idx.rec_top.get(sid)
    if sid in idx.rec_scores:
        prof["recidivism"] = {"index": idx.rec_scores.get(sid),
                              "probability": idx.rec_prob.get(sid),
                              "band": (t or {}).get("band"),
                              "drivers": (t or {}).get("drivers"),
                              "meaning": "percentile rank for allocating attention; not a finding about the person"}
    syns = idx.syndicate_of.get(sid, [])
    if syns:
        prof["syndicates"] = [{"syndicate_id": s.get("syndicate_id"), "size": s.get("size"),
                               "risk_level": s.get("risk_level"),
                               "members": [m.get("id") for m in (s.get("members") or [])][:10]} for s in syns[:2]]
    links = [l for l in (cache.get("predicted_links") or []) if sid in (l.get("a"), l.get("b"))]
    if links:
        prof["predicted_links"] = [{"with": l["b"] if l["a"] == sid else l["a"], "score": l.get("score"),
                                    "common_associates": l.get("common_associates")} for l in links]
    return prof


def _filter_cases(idx, a):
    f = idx.firs
    m = pd.Series(True, index=f.index)
    applied = {}

    def _pick(val, options):
        if not val:
            return None
        v = str(val).strip().lower()
        for o in options:
            if o.lower() == v:
                return o
        for o in options:
            if v in o.lower() or o.lower().split("(")[0].strip() in v:
                return o
        return "__none__"

    d = _pick(a.get("district"), idx.districts)
    if d:
        m &= f["district"] == d
        applied["district"] = d
    ct = _pick(a.get("crime_type"), idx.crime_types)
    if a.get("crime_type") and ct == "__none__":
        ct = detect_crime(str(a.get("crime_type")), idx) or "__none__"
    if ct:
        m &= f["crime_type"] == ct
        applied["crime_type"] = ct
    st = a.get("status")
    if st:
        st2 = detect_status(str(st)) or _pick(st, ["Under Investigation", "Charge Sheeted", "Closed"])
        m &= f["status"] == st2
        applied["status"] = st2
    if a.get("police_station"):
        m &= f["police_station"].astype(str).str.lower().str.contains(str(a["police_station"]).lower().replace(" ps", ""), na=False)
        applied["police_station"] = a["police_station"]
    for col in ("weapon_used", "vehicle_used", "time_of_day", "target_type"):
        key = col.replace("_used", "")
        val = a.get(col) or a.get(key)
        if val:
            m &= f[col].astype(str).str.lower().str.contains(str(val).lower(), na=False)
            applied[col] = val
    if a.get("year"):
        try:
            m &= f["_ts"].dt.year == int(a["year"])
            applied["year"] = int(a["year"])
        except (TypeError, ValueError):
            pass
    if a.get("date_from"):
        m &= f["_ts"] >= pd.to_datetime(a["date_from"], errors="coerce")
        applied["date_from"] = a["date_from"]
    if a.get("date_to"):
        m &= f["_ts"] <= pd.to_datetime(a["date_to"], errors="coerce")
        applied["date_to"] = a["date_to"]
    if a.get("min_severity") is not None:
        m &= f["severity_score"] >= float(a["min_severity"])
        applied["min_severity"] = a["min_severity"]
    if _truthy(a.get("has_fatalities")):
        m &= f["fatalities"].fillna(0) > 0
        applied["has_fatalities"] = True
    if a.get("keyword"):
        kw = str(a["keyword"]).lower()
        ids = {cid for cid, txt in idx.reports.items() if kw in str(txt).lower()}
        m &= f["case_id"].isin(ids)
        applied["keyword"] = a["keyword"]
    return f[m], applied


def tool_search_cases(cache, limit=8, sort_by="recent", **filters):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    sel, applied = _filter_cases(idx, filters)
    n = len(sel)
    out = {"filters_applied": applied, "total_matching": n, "share_of_corpus_pct": round(n / len(idx.firs) * 100, 2)}
    if not n:
        return out
    out["by_status"] = sel["status"].value_counts().to_dict()
    if "crime_type" not in applied:
        out["by_crime_type"] = sel["crime_type"].value_counts().head(6).to_dict()
    if "district" not in applied:
        out["by_district"] = sel["district"].value_counts().head(6).to_dict()
    out["fatalities"] = int(sel["fatalities"].fillna(0).sum())
    out["injured"] = int(sel["injured"].fillna(0).sum())
    out["avg_severity"] = round(float(sel["severity_score"].mean()), 1)
    ts = sel["_ts"].dropna()
    if len(ts):
        out["date_range"] = [f"{ts.min():%Y-%m-%d}", f"{ts.max():%Y-%m-%d}"]
    key = {"severity": "severity_score", "fatalities": "fatalities"}.get(str(sort_by), "_ts")
    rows = sel.sort_values(key, ascending=(str(sort_by) == "oldest"))
    lim = max(1, min(int(limit or 8), 25))
    out["sorted_by"] = sort_by
    out["cases"] = [{"case_id": r["case_id"], "crime_type": r["crime_type"], "district": r["district"],
                     "station": r.get("police_station"), "registered": _date(r["timestamp"]),
                     "status": r["status"], "severity": _clean(r["severity_score"]),
                     "fatalities": _clean(r["fatalities"])} for _, r in rows.head(lim).iterrows()]
    return out


GROUPABLE = {"district", "crime_type", "status", "police_station", "time_of_day", "weapon_used",
             "vehicle_used", "target_type", "year", "month", "weekday", "victim_gender"}


def tool_aggregate(cache, group_by="district", metric="count", top_n=10, **filters):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    sel, applied = _filter_cases(idx, filters)
    g = str(group_by or "district")
    if g not in GROUPABLE:
        return {"error": f"cannot group by {g}", "allowed": sorted(GROUPABLE)}
    if g == "year":
        key = sel["_ts"].dt.year
    elif g == "month":
        key = sel["_ts"].dt.strftime("%Y-%m")
    elif g == "weekday":
        key = sel["_ts"].dt.day_name()
    else:
        key = sel[g]
    metric = str(metric or "count")
    if metric in ("fatalities", "injured"):
        s = sel[metric].fillna(0).groupby(key).sum()
    elif metric in ("avg_severity", "severity"):
        s = sel["severity_score"].groupby(key).mean().round(1)
    else:
        metric = "count"
        s = key.value_counts()
    order = str(filters.get("order", "desc")).lower()
    ascending = order == "asc"
    if g in ("year", "month") and order == "chronological":
        s = s.sort_index()
    else:
        s = s.sort_values(ascending=ascending)
    n = max(1, min(int(top_n or 10), 40))
    return {"filters_applied": applied, "group_by": g, "metric": metric, "total_cases": int(len(sel)),
            "chronological": order == "chronological",
            "groups": {str(k): (float(v) if metric == "avg_severity" else int(v)) for k, v in s.head(60 if order == "chronological" else n).items()},
            "n_groups": int(s.shape[0])}


def tool_search_people(cache, name=None, gang=None, risk_level=None, rowdy_sheeter=None,
                       history_sheeter=None, active_warrant=None, min_priors=None,
                       district=None, crime_type=None, limit=10, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    df = pd.DataFrame(list(idx.criminals.values()))
    if "criminal_id" not in df:
        df["criminal_id"] = df.get("suspect_id")
    if "suspect_id" in df:
        df["criminal_id"] = df["criminal_id"].fillna(df["suspect_id"])
    m = pd.Series(True, index=df.index)
    applied = {}
    if name:
        m &= df["name"].astype(str).str.lower().str.contains(str(name).lower(), na=False) | \
             df.get("alias", pd.Series("", index=df.index)).astype(str).str.lower().str.contains(str(name).lower(), na=False)
        applied["name"] = name
    if gang:
        m &= df["gang_affiliation"].astype(str).str.lower().str.contains(str(gang).lower(), na=False)
        applied["gang"] = gang
    if risk_level:
        m &= df["risk_level"].astype(str).str.lower() == str(risk_level).lower()
        applied["risk_level"] = risk_level
    for flag, col in ((rowdy_sheeter, "rowdy_sheeter"), (history_sheeter, "history_sheet"),
                      (active_warrant, "active_warrant")):
        if flag is not None and col in df:
            m &= df[col].map(_truthy) == _truthy(flag)
            applied[col] = _truthy(flag)
    if min_priors is not None and "past_fir_count" in df:
        m &= pd.to_numeric(df["past_fir_count"], errors="coerce").fillna(0) >= float(min_priors)
        applied["min_priors"] = min_priors
    if district or crime_type:
        sel, ap = _filter_cases(idx, {"district": district, "crime_type": crime_type})
        ids = set()
        for cid in sel["case_id"]:
            ids.update(s for s, _ in idx.links_by_case.get(cid, []))
        m &= df["criminal_id"].isin(ids)
        applied.update(ap)
    sel = df[m]
    out = {"filters_applied": applied, "total_matching": int(len(sel))}
    if len(sel):
        out["by_risk_level"] = sel["risk_level"].value_counts().to_dict()
        gangs = sel["gang_affiliation"].dropna().astype(str)
        if len(gangs):
            out["top_gangs"] = gangs.value_counts().head(6).to_dict()
        for col, label in (("rowdy_sheeter", "rowdy_sheeters"), ("history_sheet", "history_sheeters"),
                           ("active_warrant", "with_active_warrant")):
            if col in sel:
                out[label] = int(sel[col].map(_truthy).sum())
        srt = pd.to_numeric(sel.get("past_fir_count"), errors="coerce").fillna(0)
        top = sel.assign(_p=srt).sort_values("_p", ascending=False).head(max(1, min(int(limit or 10), 20)))
        out["people"] = [_person_brief(str(s), idx) for s in top["criminal_id"]]
    return out


def tool_get_officer(cache, officer_ref=None, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    oid = resolve_officer(officer_ref, idx)
    if not oid:
        hits = [i for k, i in idx.names.get(_norm_key(officer_ref), []) if k == "officer"]
        if len(hits) == 1:
            oid = hits[0]
        elif hits:
            return {"ambiguous": True, "candidates": [{"officer_id": h, **_rec(idx.officers[h], ["name", "rank", "station"])} for h in hits]}
    if not oid:
        return {"error": f"No officer matching '{officer_ref}'."}
    o = idx.officers[oid]
    out = {"officer_id": oid}
    out.update(_rec(o, ["name", "rank", "station", "district", "specialization", "cases_assigned",
                        "closure_rate", "commendations", "appointment_date"]))
    f = idx.firs
    mine = f[f["officer_id"].astype(str) == oid]
    out["cases_as_io_in_corpus"] = int(len(mine))
    if len(mine):
        out["by_status"] = mine["status"].value_counts().to_dict()
        out["by_crime_type"] = mine["crime_type"].value_counts().head(5).to_dict()
        out["recent_cases"] = [{"case_id": r["case_id"], "crime_type": r["crime_type"], "status": r["status"],
                                "registered": _date(r["timestamp"])}
                               for _, r in mine.sort_values("_ts", ascending=False).head(5).iterrows()]
    return out


def tool_search_officers(cache, district=None, rank=None, specialization=None, sort_by="closure_rate", limit=8, **_):
    idx = _index(cache)
    if idx is None:
        return {"error": "corpus not loaded"}
    df = pd.DataFrame(list(idx.officers.values()))
    if df.empty:
        return {"total_matching": 0}
    for col, val in (("district", district), ("rank", rank), ("specialization", specialization)):
        if val:
            df = df[df[col].astype(str).str.lower().str.contains(str(val).lower(), na=False)]
    key = sort_by if sort_by in ("closure_rate", "cases_assigned", "commendations") else "closure_rate"
    df = df.sort_values(key, ascending=False)
    return {"total_matching": int(len(df)), "sorted_by": key,
            "by_rank": df["rank"].value_counts().to_dict() if len(df) else {},
            "officers": [_rec(r, ["officer_id", "name", "rank", "station", "district", "specialization",
                                  "cases_assigned", "closure_rate", "commendations"])
                         for r in df.head(max(1, min(int(limit or 8), 20))).to_dict("records")]}


REGISTRY_FIELDS = {
    "vehicles": ("vehicles", ["vehicle_number", "make_model", "theft_district", "theft_date",
                              "recovery_status", "linked_case_id", "owner_name"]),
    "cybercrime": ("cybercrime", ["case_id", "upi_ids", "bank_accounts", "phone_numbers", "ip_addresses",
                                  "urls", "crypto_wallet", "fraud_amount", "victim_count"]),
    "missing": ("missing", ["person_id", "name", "age", "gender", "last_seen_district", "date_missing",
                            "found_status"]),
    "courts": ("courts", ["court_case_no", "fir_number", "court_name", "chargesheet_status",
                          "conviction_status", "next_hearing"]),
    "complaints": ("complaints", ["complaint_id", "district", "emergency_type", "response_time_min",
                                  "assigned_officer", "resolution_status", "linked_case_id"]),
    "cctv": ("cctv", ["camera_id", "district", "timestamp", "detected_faces", "detected_vehicle", "case_link"]),
}


def tool_search_registry(cache, kind="vehicles", query=None, district=None, status=None, limit=8, **_):
    kind = str(kind or "").lower()
    if kind in ("vehicle", "stolen_vehicles"):
        kind = "vehicles"
    if kind in ("cyber", "fraud"):
        kind = "cybercrime"
    if kind in ("missing_persons", "missing_person"):
        kind = "missing"
    if kind in ("court", "trials", "trial"):
        kind = "courts"
    if kind in ("dial112", "calls", "112"):
        kind = "complaints"
    if kind not in REGISTRY_FIELDS:
        return {"error": f"unknown registry '{kind}'", "allowed": sorted(REGISTRY_FIELDS)}
    key, cols = REGISTRY_FIELDS[kind]
    df = _frame(cache, key)
    if df is None:
        return {"error": f"{kind} registry not loaded"}
    m = pd.Series(True, index=df.index)
    if query:
        q = re.sub(r"[\s-]", "", str(query).lower())
        hay = df[[c for c in cols if c in df]].astype(str).apply(lambda s: s.str.lower().str.replace(r"[\s-]", "", regex=True))
        m &= hay.apply(lambda s: s.str.contains(re.escape(q), na=False)).any(axis=1)
    if district:
        dcols = [c for c in ("district", "theft_district", "last_seen_district") if c in df]
        if dcols:
            m &= df[dcols[0]].astype(str).str.lower().str.contains(str(district).lower(), na=False)
    if status:
        scols = [c for c in ("recovery_status", "found_status", "conviction_status", "resolution_status") if c in df]
        if scols:
            col = df[scols[0]].astype(str).str.lower()
            st = str(status).lower()
            exact = col == st
            m &= exact if exact.any() else col.str.contains(st, na=False)
    sel = df[m]
    out = {"registry": kind, "query": query, "district": district, "status": status,
           "total_records": int(len(df)), "total_matching": int(len(sel))}
    for sc in ("recovery_status", "found_status", "conviction_status", "resolution_status", "emergency_type"):
        if sc in sel and len(sel):
            out[f"by_{sc}"] = sel[sc].astype(str).value_counts().head(6).to_dict()
    if kind == "cybercrime" and len(sel):
        out["total_fraud_rs"] = int(pd.to_numeric(sel["fraud_amount"], errors="coerce").fillna(0).sum())
        out["total_victims"] = int(pd.to_numeric(sel["victim_count"], errors="coerce").fillna(0).sum())
    if kind == "complaints" and len(sel):
        out["avg_response_min"] = round(float(pd.to_numeric(sel["response_time_min"], errors="coerce").mean()), 1)
    out["records"] = [{c: _clean(r.get(c)) for c in cols if c in r}
                      for r in sel.head(max(1, min(int(limit or 8), 20))).to_dict("records")]
    return out


def tool_network(cache, entity=None, target=None, **_):
    idx = _index(cache)
    G = _graph()
    if idx is None or G is None:
        return {"error": "link graph not loaded"}

    def _node(ref):
        if ref is None:
            return None
        s = str(ref)
        for cand in (resolve_suspect(s, idx) if re.search(r"sus", s, re.I) or not RE_CRIMENO.search(s) else None,
                     resolve_case(s, idx), resolve_officer(s, idx) if "kgid" in s.lower() else None):
            if cand and G.has_node(cand):
                return cand
        hits = [i for k, i in idx.names.get(_norm_key(s), []) if k == "offender"]
        if len(hits) == 1 and G.has_node(hits[0]):
            return hits[0]
        return s if G.has_node(s) else None

    def _label(n):
        d = G.nodes[n]
        t = d.get("type", "Node")
        if t == "Suspect":
            return {"id": n, "type": t, "name": idx.criminals.get(n, {}).get("name") or d.get("name")}
        if t == "Case":
            return {"id": n, "type": t, "crime_type": d.get("crime_type"), "district": d.get("district")}
        return {"id": n, "type": t, **{k: v for k, v in d.items() if k in ("name", "label")}}

    a = _node(entity)
    if not a:
        return {"error": f"'{entity}' is not a node in the link graph"}
    if target:
        b = _node(target)
        if not b:
            return {"error": f"'{target}' is not a node in the link graph"}
        import networkx as nx
        try:
            path = nx.shortest_path(G, a, b)
        except nx.NetworkXNoPath:
            return {"from": a, "to": b, "connected": False,
                    "note": "No chain of shared cases, identifiers or associates links them."}
        except Exception as e:
            return {"error": str(e)}
        return {"from": a, "to": b, "connected": True, "hops": len(path) - 1,
                "path": [_label(n) for n in path[:12]]}
    nbrs = list(G.neighbors(a))
    by_type = {}
    for n in nbrs:
        by_type.setdefault(G.nodes[n].get("type", "Node"), []).append(n)
    out = {"entity": _label(a), "degree": len(nbrs),
           "neighbours_by_type": {t: len(v) for t, v in by_type.items()},
           "neighbours": {t: [_label(n) for n in v[:8]] for t, v in by_type.items()}}
    if G.nodes[a].get("type") == "Suspect":
        two = set()
        for c in by_type.get("Case", []):
            two.update(x for x in G.neighbors(c) if x != a and G.nodes[x].get("type") == "Suspect")
        out["second_degree_suspects"] = len(two)
    return out


def tool_insight(cache, topic="overview", district=None, crime_type=None, **_):
    topic = str(topic or "overview").lower()
    idx = _index(cache)
    d = detect_district(district, idx) if (district and idx) else None
    if topic in ("forecast", "prediction", "next_week"):
        fc = cache.get("forecast") or {}
        o = fc.get("overall") or {}
        out = {"model": "RidgeCV + Fourier seasonality (weekly counts)",
               "statewide": {k: o.get(k) for k in ("next_week", "trend", "change_pct", "avg_weekly")},
               "forecast_weeks": fc.get("future_labels"),
               "statewide_next_8_weeks": o.get("forecast"),
               "interval_coverage": fc.get("interval_coverage_realised")}
        bd = fc.get("by_district") or {}
        if d and d in bd:
            out["district"] = {d: {k: bd[d].get(k) for k in ("next_week", "trend", "change_pct", "avg_weekly")}}
        rising = sorted([(k, v.get("change_pct", 0)) for k, v in bd.items() if v.get("trend") == "rising"],
                        key=lambda x: -x[1])[:5]
        out["districts_rising"] = dict(rising)
        bc = fc.get("by_crime_type") or {}
        ct = detect_crime(crime_type, idx) if (crime_type and idx) else None
        if ct and ct in bc:
            out["crime_type"] = {ct: {k: bc[ct].get(k) for k in ("next_week", "trend", "change_pct", "avg_weekly")}}
        out["by_crime_type"] = {k: {"next_week": v.get("next_week"), "trend": v.get("trend")} for k, v in bc.items()}
        return out
    if topic in ("hotspots", "hotspot", "clusters", "zones"):
        cl = cache.get("crime_clusters") or []
        if d:
            cl = [c for c in cl if c.get("district") == d]
        return {"dbscan_zones": len(cache.get("crime_clusters") or []), "district_filter": d,
                "zones": [{k: c.get(k) for k in ("id", "district", "cases", "dominant_crime", "radius_km",
                                                 "open_cases", "peak_time", "avg_severity")} for c in cl[:6]],
                "kde_predicted_hotspots": [{k: h.get(k) for k in ("lat", "lng", "intensity")}
                                           for h in (cache.get("predicted_hotspots") or [])[:5]]}
    if topic in ("trends", "trend", "alerts", "spikes"):
        al = cache.get("trend_alerts") or []
        if d:
            al = [a for a in al if a.get("district") == d]
        return {"method": "last 30 days vs historical baseline", "alerts": al[:8]}
    if topic in ("anomalies", "anomaly"):
        an = cache.get("anomalies") or []
        return {"method": "Isolation Forest", "count": len(an), "anomalies": an[:8]}
    if topic in ("recidivism", "risk", "watchlist"):
        rec = cache.get("recidivism") or {}
        return {"model": "calibrated re-offence within 365 days", "bands": rec.get("bands"),
                "population": rec.get("population"), "index_meaning": rec.get("index_meaning"),
                "top_by_id": [{"criminal_id": t.get("criminal_id"), "risk_index": t.get("risk_index"),
                               "band": t.get("band"), "drivers": t.get("drivers")} for t in (rec.get("top") or [])[:8]],
                "policy": "names are withheld from ranked lists; open the registry for the gated view"}
    if topic in ("pendency", "backlog", "overdue"):
        p = cache.get("pendency") or {}
        bd = p.get("by_district") or []
        if d:
            bd = [x for x in bd if x.get("name") == d]
        cases = p.get("cases") or []
        if d:
            cases = [c for c in cases if c.get("district") == d]
        return {"summary": p.get("summary"), "by_district": bd[:8],
                "longest_expected_delay": [{k: c.get(k) for k in ("case_id", "district", "crime_type", "days_open",
                                                                  "expected_additional_days")}
                                           for c in sorted(cases, key=lambda c: -(c.get("expected_additional_days") or 0))[:5]]}
    if topic in ("dispatch", "patrol", "deployment", "resources"):
        rows = cache.get("dispatch") or []
        if d:
            rows = [r for r in rows if r.get("district") == d]
        return {"method": rows[0].get("risk_method") if rows else None,
                "districts": [{k: r.get(k) for k in ("district", "risk_score", "risk_level", "recommended_officers",
                                                     "open_cases", "recent_count", "high_severity_cases")} for r in rows[:8]],
                "total_recommended_officers": int(sum(r.get("recommended_officers", 0) for r in (cache.get("dispatch") or [])))}
    if topic in ("socioeconomic", "socio", "demographics", "census"):
        s = cache.get("socioeconomic") or {}
        out = {"insights": s.get("insights"), "correlations": s.get("correlations")}
        if d:
            out["district"] = next((x for x in (s.get("districts") or []) if x.get("district") == d), None)
        return out
    if topic in ("operational_index", "risk_index", "district_index"):
        oi = cache.get("operational_index") or {}
        rows = oi.get("districts") or []
        if d:
            rows = [r for r in rows if r.get("district") == d]
        return {"method": oi.get("method"), "caveat": oi.get("caveat"),
                "districts": [{k: r.get(k) for k in ("district", "index", "band", "raw")} for r in rows[:8]]}
    if topic in ("lifecycle", "funnel"):
        lc = cache.get("lifecycle") or {}
        return {"cohort": lc.get("cohort"), "note": lc.get("note")}
    if topic in ("predicted_links", "links", "alliances"):
        return {"method": "Adamic-Adar on the co-offender graph",
                "links": [{k: l.get(k) for k in ("a", "a_name", "b", "b_name", "score", "common_associates",
                                                 "same_gang", "gang")} for l in (cache.get("predicted_links") or [])[:8]]}
    if topic in ("syndicates", "gangs", "communities"):
        syn = sorted(cache.get("syndicate_communities") or [], key=lambda s: -(s.get("size") or 0))
        return {"method": "Louvain on repeat co-offending", "count": len(syn),
                "largest": [{"syndicate_id": s.get("syndicate_id"), "size": s.get("size"),
                             "risk_level": s.get("risk_level"),
                             "member_ids": [m.get("id") for m in (s.get("members") or [])][:10]} for s in syn[:6]]}
    if topic in ("cyber_rings", "cyber", "fraud_rings"):
        ext = cache.get("extended") or {}
        return {"cyber_cases": ext.get("cyber_cases"), "total_fraud_rs": ext.get("cyber_total_fraud"),
                "victims": ext.get("cyber_victims"),
                "rings": [{k: r.get(k) for k in ("identifier", "linked_cases", "total_fraud")} | {"sample_cases": (r.get("cases") or [])[:4]}
                          for r in (ext.get("cyber_rings") or [])[:6]]}
    if topic in ("courts", "conviction", "trials"):
        c = _frame(cache, "courts")
        if c is None:
            return {"error": "court registry not loaded"}
        vc = c["conviction_status"].value_counts().to_dict()
        dec = vc.get("Convicted", 0) + vc.get("Acquitted", 0)
        return {"court_records": int(len(c)), "by_outcome": vc,
                "conviction_rate_of_decided_pct": round(vc.get("Convicted", 0) / dec * 100, 1) if dec else None,
                "by_court": c["court_name"].value_counts().head(8).to_dict()}
    # overview
    f = idx.firs if idx else _frame(cache, "firs")
    ext = cache.get("extended") or {}
    out = {"total_firs": int(len(f)), "by_status": f["status"].value_counts().to_dict(),
           "top_crime_types": f["crime_type"].value_counts().head(5).to_dict(),
           "top_districts": f["district"].value_counts().head(5).to_dict(),
           "offenders": len(idx.criminals) if idx else None,
           "fatalities": int(f["fatalities"].fillna(0).sum()), "injured": int(f["injured"].fillna(0).sum()),
           "rowdy_sheeters": ext.get("rowdy_sheeters"), "history_sheeters": ext.get("history_sheeters"),
           "active_warrants": ext.get("active_warrants"), "stolen_vehicles": ext.get("vehicles_total"),
           "vehicle_recovery_rate_pct": ext.get("vehicle_recovery_rate"), "officers": ext.get("officers_total")}
    if idx is not None:
        ts = f["_ts"].dropna()
        if len(ts):
            out["date_range"] = [f"{ts.min():%Y-%m-%d}", f"{ts.max():%Y-%m-%d}"]
    return out


def tool_model_info(cache, model=None, **_):
    import agent_answers
    reg = agent_answers._registry() or {}
    models = reg.get("models") or {}
    if not models:
        return {"error": "model registry unavailable"}
    pick = None
    if model:
        q = str(model).lower().replace(" ", "_")
        for name in models:
            if name in q or q in name or name.split("_")[0] in q:
                pick = name
                break
    if not pick:
        return {"models": {n: {"headline": m.get("headline"),
                               "key_metrics": {k: (m.get("metrics") or {}).get(k) for k in
                                               ("accuracy", "roc_auc", "mase", "c_index", "silhouette", "f1_macro")
                                               if (m.get("metrics") or {}).get(k) is not None}}
                           for n, m in models.items()},
                "source": "artifacts/model_registry.json"}
    m = models[pick]
    mt = m.get("metrics") or {}
    return {"model": pick, "headline": m.get("headline"),
            "metrics": {k: v for k, v in mt.items() if isinstance(v, (int, float))},
            "baselines": (m.get("baselines") or [])[:5], "notes": (m.get("notes") or [])[:4],
            "card": m.get("card") or m.get("model_card"), "source": "artifacts/model_registry.json"}


def tool_project_knowledge(cache, topic="overview", **_):
    return {"sections": copilot_knowledge.lookup(topic)}


# Tools that read one specific record; an answer built from them outranks a generic one.
ENTITY_TOOLS = {"get_case", "get_person", "get_officer", "network", "search_registry"}

NAV_ACTIONS = {"open_case", "open_suspect", "show_heatmap", "show_network", "show_dispatch",
               "show_analytics", "show_models", "recent_cases", "show_registry", "show_anomalies"}


TOOLS = {
    "get_case": (tool_get_case, "Full dossier of ONE FIR/case: narrative, accused (with ids, priors, flags), victim, complainant, investigating officer, arrests, chargesheet, court stage, vehicles, cyber identifiers, CCTV, Dial-112, pendency model, similar cases.",
                 {"case_ref": {"type": "string", "description": "18-digit CrimeNo, FIR-<CaseMasterID>, CaseMasterID or CaseNo"}}, ["case_ref"]),
    "get_person": (tool_get_person, "Profile of ONE offender by SUS-id or full name: registry flags (rowdy-sheeter, history-sheet, warrant, gang), every case they are named in, crime types, districts, court outcomes, co-offenders, recidivism index and drivers, syndicates, predicted links. Also reports if the name matches officers, missing persons or victims.",
                   {"person_ref": {"type": "string", "description": "SUS-#### or a full name"}}, ["person_ref"]),
    "search_cases": (tool_search_cases, "Filter FIRs and return the exact count, status/crime/district breakdowns, casualty totals, date range and the top matching cases.",
                     {"district": {"type": "string"}, "crime_type": {"type": "string"},
                      "status": {"type": "string", "description": "Under Investigation | Charge Sheeted | Closed"},
                      "police_station": {"type": "string"}, "year": {"type": "integer"},
                      "date_from": {"type": "string", "description": "YYYY-MM-DD"}, "date_to": {"type": "string"},
                      "min_severity": {"type": "number"}, "has_fatalities": {"type": "boolean"},
                      "weapon": {"type": "string"}, "vehicle": {"type": "string"}, "time_of_day": {"type": "string"},
                      "target_type": {"type": "string"}, "keyword": {"type": "string", "description": "word to find in FIR narratives"},
                      "sort_by": {"type": "string", "enum": ["recent", "oldest", "severity", "fatalities"]},
                      "limit": {"type": "integer"}}, []),
    "aggregate": (tool_aggregate, "Group FIRs and rank: counts, fatalities, injured or average severity by district, crime_type, status, police_station, time_of_day, weapon_used, vehicle_used, target_type, year, month, weekday or victim_gender — with optional filters.",
                  {"group_by": {"type": "string"}, "metric": {"type": "string", "enum": ["count", "fatalities", "injured", "avg_severity"]},
                   "top_n": {"type": "integer"}, "order": {"type": "string", "enum": ["desc", "asc", "chronological"]},
                   "district": {"type": "string"}, "crime_type": {"type": "string"}, "status": {"type": "string"},
                   "year": {"type": "integer"}, "police_station": {"type": "string"}}, ["group_by"]),
    "search_people": (tool_search_people, "Filter the offender registry (9,000 people): by name fragment, gang, risk level, rowdy-sheeter, history-sheeter, active warrant, minimum priors, or by district/crime type of their cases. Returns counts, breakdowns and top people by priors.",
                      {"name": {"type": "string"}, "gang": {"type": "string"}, "risk_level": {"type": "string"},
                       "rowdy_sheeter": {"type": "boolean"}, "history_sheeter": {"type": "boolean"},
                       "active_warrant": {"type": "boolean"}, "min_priors": {"type": "integer"},
                       "district": {"type": "string"}, "crime_type": {"type": "string"}, "limit": {"type": "integer"}}, []),
    "get_officer": (tool_get_officer, "One police officer by KGID or name: rank, station, specialization, closure rate, commendations and the cases they investigate.",
                    {"officer_ref": {"type": "string"}}, ["officer_ref"]),
    "search_officers": (tool_search_officers, "List/rank officers by district, rank or specialization, sorted by closure_rate, cases_assigned or commendations.",
                        {"district": {"type": "string"}, "rank": {"type": "string"}, "specialization": {"type": "string"},
                         "sort_by": {"type": "string"}, "limit": {"type": "integer"}}, []),
    "search_registry": (tool_search_registry, "Search a side registry: vehicles (stolen vehicles), cybercrime (UPI/bank/phone/IP/URL/wallet identifiers), missing (missing persons), courts (trials), complaints (Dial-112 calls), cctv. Free-text query matches any field.",
                        {"kind": {"type": "string", "enum": sorted(REGISTRY_FIELDS)}, "query": {"type": "string"},
                         "district": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"}}, ["kind"]),
    "network": (tool_network, "Link-graph query. With only `entity`: its neighbours (cases, co-offenders, identifiers) and degree. With `target` too: the shortest chain connecting the two.",
                {"entity": {"type": "string", "description": "SUS-id, case id or name"}, "target": {"type": "string"}}, ["entity"]),
    "insight": (tool_insight, "Platform analytics outputs: overview, forecast, hotspots, trends, anomalies, recidivism, pendency, dispatch, socioeconomic, operational_index, lifecycle, predicted_links, syndicates, cyber_rings, courts. Optional district / crime_type focus.",
                {"topic": {"type": "string"}, "district": {"type": "string"}, "crime_type": {"type": "string"}}, ["topic"]),
    "model_info": (tool_model_info, "Metrics, baselines, notes and model card for the ML models (narrative_classifier, crime_forecast, recidivism_risk, conviction_outcome, case_closure, case_pendency, hotspot_clusters). Omit model for all.",
                   {"model": {"type": "string"}}, []),
    "project_knowledge": (tool_project_knowledge, "Documentation about the IntelHub platform itself: overview, pages, data, models, copilot, governance, security, tech, domain (policing terms).",
                          {"topic": {"type": "string"}}, ["topic"]),
    "navigate": (None, "Move the officer's screen. Call at most once, only when showing a screen helps (e.g. after answering about a case, open it).",
                 {"action": {"type": "string", "enum": sorted(NAV_ACTIONS)},
                  "case_id": {"type": "string"}, "suspect_id": {"type": "string"}, "district": {"type": "string"},
                  "crime_type": {"type": "string"},
                  "kind": {"type": "string", "description": "registry tab: criminals|vehicles|missing|cybercrime|officers|courts"}},
                 ["action"]),
}


def tool_specs():
    return [{"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": req}}}
        for name, (_fn, desc, props, req) in TOOLS.items()]


def run_tool(name, args, cache):
    spec = TOOLS.get(name)
    if not spec or spec[0] is None:
        return {"error": f"unknown tool {name}"}
    try:
        return spec[0](cache, **(args or {}))
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        LOG.warning("copilot tool %s failed: %s", name, e)
        return {"error": f"{name} failed: {e}"}


# =============================================================================
# Privacy for egress
# =============================================================================
NAME_KEYS = {"name", "victim_name", "a_name", "b_name", "owner_name", "ComplainantName", "AccusedName"}
DROP_KEYS = {"owner_phone", "family_contact", "caller_phone", "photo_url"}


def mask_for_llm(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in DROP_KEYS:
                continue
            if k in NAME_KEYS and isinstance(v, str):
                out[k] = privacy.mask(v)
            else:
                out[k] = mask_for_llm(v)
        return out
    if isinstance(obj, list):
        return [mask_for_llm(v) for v in obj]
    return obj


def _dump(obj, limit=6000):
    s = json.dumps(obj, default=str, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= limit else s[:limit] + "…(truncated)"


# =============================================================================
# Planner — which tools a question needs (used for prefetch and offline answers)
# =============================================================================
TOPIC_CUES = [
    ("forecast", ("forecast", "next week", "next month", "predict", "projection", "expected crime", "coming weeks")),
    ("trends", ("trend", "spike", "spiking", "rising", "surge", "emerging", "increase", "heating up", "going up")),
    ("hotspots", ("hotspot", "hot spot", "cluster", "dbscan", "zone", "danger area", "red zone")),
    ("anomalies", ("anomal", "unusual", "outlier", "isolation forest")),
    ("pendency", ("pendency", "backlog", "overdue", "delay", "90 day", "90-day", "pending chargesheet", "sitting")),
    ("dispatch", ("dispatch", "patrol", "deploy", "deployment", "how many police", "reinforce", "resource")),
    ("socioeconomic", ("socio", "unemploy", "literacy", "income", "poverty", "census", "demograph", "why crime")),
    ("operational_index", ("operational index", "operational risk", "district index", "risk index of district")),
    ("lifecycle", ("lifecycle", "funnel", "attrition", "conversion")),
    ("predicted_links", ("predicted link", "alliance", "adamic", "emerging link", "future link")),
    ("syndicates", ("syndicate", "louvain", "community", "communities", "organised", "organized crime", "gangs")),
    ("cyber_rings", ("cyber ring", "fraud ring", "mule", "rings")),
    ("courts", ("conviction", "acquit", "court", "trial", "verdict")),
    ("recidivism", ("recidiv", "reoffend", "re-offend", "repeat offender", "watchlist", "risk band")),
]


def _cue(low, words):
    return any(w in low for w in words)


def plan(text, idx, ents):
    """Ordered (tool, args) calls that a question needs."""
    low = _kannada_to_english(text or "").lower()
    text = _kannada_to_english(text or "")
    calls = []
    for c in ents["cases"][:3]:
        calls.append(("get_case", {"case_ref": c}))
    people = list(ents["suspects"])
    for n in ents["names"]:
        offs = [i for k, i in n["matches"] if k == "offender"]
        only_officer = all(k == "officer" for k, _ in n["matches"])
        if (not offs or len(offs) > 1) and not only_officer:
            calls.append(("get_person", {"person_ref": n["name"]}))
    for s in people[:3]:
        calls.append(("get_person", {"person_ref": s}))
    if len(people) >= 2 and _cue(low, ("connect", "link", "relat", "path", "between", "know each", "associat")):
        return [("network", {"entity": people[0], "target": people[1]})]
    for o in ents["officers"][:2]:
        calls.append(("get_officer", {"officer_ref": o}))
    for v in ents["vehicles"][:2]:
        calls.append(("search_registry", {"kind": "vehicles", "query": v}))
    for i in ents["identifiers"][:2]:
        calls.append(("search_registry", {"kind": "cybercrime", "query": i}))
    for mp in ents["missing"][:2]:
        calls.append(("search_registry", {"kind": "missing", "query": mp}))
    if calls:
        return calls

    filters = {k: ents[k] for k in ("district", "crime_type", "status", "year") if ents.get(k)}
    if ents.get("station"):
        filters["police_station"] = ents["station"]
    if _cue(low, ("death", "fatal", "killed", "died")):
        filters["has_fatalities"] = True

    definitional = not filters and not _cue(low, ("situation", "status", "rate", "level", "how many", "count",
                                                  "number", "trend", "list", "which", "top"))
    if copilot_knowledge.is_platform_question(text) or definitional and re.search(
            r"^\s*(what is|what's|what are|what does|define|meaning of|explain)\b.{0,12}\b(a |an |the )?"
            r"(rowdy|history.?sheet|fir|chargesheet|charge sheet|warrant|ipc|bns|cctns|mo\b|modus|io\b|"
            r"commissionerate|recidivism|pendency|kde|dbscan|adamic|louvain|isolation forest|severity)", low):
        return [("project_knowledge", {"topic": text})]
    if _cue(low, ("model", "accura", "auc", "precision", "recall", "f1", "how reliable", "how good is", "baseline")) \
            and not _cue(low, ("vehicle", "make model")):
        pick = next((m for k, m in (("recidiv", "recidivism_risk"), ("classif", "narrative_classifier"),
                                    ("narrative", "narrative_classifier"), ("forecast", "crime_forecast"),
                                    ("conviction", "conviction_outcome"), ("trial", "conviction_outcome"),
                                    ("closure", "case_closure"), ("progress", "case_closure"),
                                    ("pendency", "case_pendency"), ("survival", "case_pendency"),
                                    ("hotspot", "hotspot_clusters"), ("cluster", "hotspot_clusters")) if k in low), None)
        return [("model_info", {"model": pick})]

    # People-registry questions
    if _cue(low, ("rowdy", "sheeter", "warrant", "history sheet", "history-sheet", "gang", "offenders",
                  "criminals", "repeat offender")) and not _cue(low, ("recidiv",)):
        args = {}
        if "rowdy" in low:
            args["rowdy_sheeter"] = True
        if "warrant" in low:
            args["active_warrant"] = True
        if "history" in low:
            args["history_sheeter"] = True
        gm = re.search(r"([A-Z][\w-]+(?:\s[A-Z][\w-]+)?)\s+gang", text or "")
        if gm:
            args["gang"] = gm.group(1)
        if filters.get("district"):
            args["district"] = filters["district"]
        if filters.get("crime_type"):
            args["crime_type"] = filters["crime_type"]
        return [("search_people", args)]

    if _cue(low, ("officer", "inspector", "constable", "dysp", " si ", "io ")):
        return [("search_officers", {"district": filters.get("district"),
                                     "sort_by": "cases_assigned" if "most cases" in low else "closure_rate"})]
    if filters.get("crime_type", "").startswith("Cyber") and _cue(low, ("money", "lost", "amount", "rupee", " rs", "lakh",
                                                                        "crore", "upi", "bank", "victims", "ring")):
        return [("insight", {"topic": "cyber_rings"})] if "ring" in low else \
            [("search_registry", {"kind": "cybercrime", "district": filters.get("district")})]
    reg_status = next((st for st, words in (("not recovered", ("not recovered", "unrecovered", "still stolen")),
                                            ("recovered", ("recovered",)), ("missing", ("still missing", "not found", "untraced")),
                                            ("found", ("found", "traced")), ("deceased", ("dead", "deceased")),
                                            ("pending", ("pending",)), ("resolved", ("resolved",)))
                       if _cue(low, words)), None)
    for kind, words in (("vehicles", ("vehicle", "car", "bike", "stolen")),
                        ("missing", ("missing", "lapata", "found")),
                        ("complaints", ("112", "dial", "emergency call", "response time")),
                        ("cctv", ("cctv", "camera"))):
        if _cue(low, words) and not filters.get("crime_type"):
            st = reg_status
            if st and kind == "vehicles" and st not in ("recovered", "not recovered"):
                st = None
            if st and kind == "missing" and st not in ("missing", "found", "deceased"):
                st = None
            return [("search_registry", {"kind": kind, "district": filters.get("district"), "status": st})]

    for topic, words in TOPIC_CUES:
        if _cue(low, words):
            return [("insight", {"topic": topic, "district": filters.get("district"),
                                 "crime_type": filters.get("crime_type")})]

    m = re.search(r"\b(which|what|top|most|highest|lowest|least|rank|compare|breakdown|distribution|per|by)\b", low)
    group = None
    for g, words in (("police_station", ("station",)), ("district", ("district", "region", "area", "city")),
                     ("crime_type", ("crime type", "type of crime", "kind of crime", "category", "crimes are", "crime is")),
                     ("time_of_day", ("time of day", "what time", "hour", "night", "when do")),
                     ("weapon_used", ("weapon",)), ("vehicle_used", ("vehicle used", "getaway")),
                     ("target_type", ("target",)), ("month", ("month", "monthly", "seasonal")),
                     ("year", ("year", "yearly", "annual")), ("weekday", ("weekday", "day of week", "which day")),
                     ("status", ("status",))):
        if _cue(low, words) and g not in filters:
            group = g
            break
    if m and group:
        metric = "fatalities" if _cue(low, ("death", "fatal", "killed", "murders")) and group != "crime_type" else "count"
        superlative = _cue(low, ("most", "highest", "lowest", "least", "fewest", "safest", "top", "worst", "peak", "busiest"))
        args = dict(filters, group_by=group, metric=metric,
                    order="asc" if _cue(low, ("lowest", "least", "fewest", "safest")) else
                    ("desc" if superlative or group not in ("year", "month") else "chronological"))
        args.pop("has_fatalities", None)
        return [("aggregate", args)]

    if filters or _cue(low, ("how many", "count", "number of", "total", "cases", "firs", "list")):
        sort = "severity" if _cue(low, ("severe", "serious", "worst", "dangerous")) else \
               "fatalities" if _cue(low, ("deadl", "fatal")) else "recent"
        if filters or _cue(low, ("how many", "count", "number of")):
            return [("search_cases", dict(filters, sort_by=sort))]
    if _cue(low, ("summary", "overview", "situation", "status of crime", "brief me", "briefing", "statistics", "stats")):
        return [("insight", {"topic": "overview"})]
    return []


# =============================================================================
# Deterministic answer composition
# =============================================================================
def _kv(d, n=5):
    return ", ".join(f"{k} ({_fmt_int(v) if isinstance(v, (int, float)) else v})" for k, v in list((d or {}).items())[:n])


def _say_case(c):
    if c.get("error"):
        return c["error"]
    lines = [f"**FIR {c['case_id']}** — {c.get('crime_type')} at {c.get('police_station')}, "
             f"{c.get('district')} district, registered {str(c.get('registered'))[:16]}."]
    lines.append(f"- Status: **{c.get('status')}** · severity {c.get('severity_score')}/100 · "
                 f"{c.get('time_of_day')} · target: {c.get('target_type')}")
    mo = [x for x in (f"weapon {c.get('weapon_used')}" if c.get("weapon_used") not in (None, "None") else None,
                      f"vehicle {c.get('vehicle_used')}" if c.get("vehicle_used") not in (None, "None") else None,
                      c.get("ipc_bns_sections")) if x]
    if mo:
        lines.append("- MO / sections: " + " · ".join(mo))
    if c.get("fatalities") or c.get("injured"):
        lines.append(f"- Casualties: {c.get('fatalities') or 0} killed, {c.get('injured') or 0} injured; "
                     f"{c.get('witnesses') or 0} witnesses cited")
    v = c.get("victim") or {}
    if v.get("name"):
        lines.append(f"- Victim: {v['name']}" + (f", {v.get('age')}" if v.get("age") else "")
                     + (f" ({v.get('gender')})" if v.get("gender") else "")
                     + (" — police personnel" if v.get("is_police_personnel") else ""))
    if c.get("complainant", {}).get("name"):
        lines.append(f"- Complainant: {c['complainant']['name']}")
    acc = c.get("accused") or []
    if acc:
        parts = []
        for a in acc[:6]:
            flags = [f for f, on in (("rowdy-sheeter", a.get("rowdy_sheeter")), ("active warrant", a.get("active_warrant")))
                     if on]
            parts.append(f"{a.get('name')} ({a['suspect_id']}, {a.get('relationship')}"
                         + (f", {a.get('prior_firs')} priors" if a.get("prior_firs") is not None else "")
                         + (", " + ", ".join(flags) if flags else "") + ")")
        lines.append(f"- Accused ({len(acc)}): " + "; ".join(parts))
    else:
        lines.append("- No accused are linked to this case.")
    io = c.get("investigating_officer") or {}
    if io:
        lines.append(f"- IO: {io.get('name') or ''} ({io.get('officer_id')}" + (f", {io.get('rank')}" if io.get("rank") else "") + ")")
    prog = [f"{c['arrests']['count']} arrest(s)" + (f" from {c['arrests']['first_date']}" if c['arrests'].get('first_date') else "")] if c.get("arrests") else []
    if c.get("chargesheet"):
        prog.append(f"chargesheet filed {c['chargesheet'].get('filed_on')}")
    if c.get("court"):
        ct = c["court"]
        prog.append(f"{ct.get('court_name')}: {ct.get('conviction_status')}"
                    + (f", next hearing {ct.get('next_hearing')}" if ct.get("conviction_status") == "Pending Trial" and ct.get("next_hearing") else ""))
    if prog:
        lines.append("- Progress: " + " → ".join(prog))
    if c.get("pendency_model"):
        p = c["pendency_model"]
        lines.append(f"- Pendency model: open {p.get('days_open')} days, ~{round(p.get('expected_additional_days') or 0)} more days expected before chargesheet")
    if c.get("stolen_vehicles"):
        lines.append("- Linked stolen vehicle: " + "; ".join(f"{x.get('vehicle_number')} {x.get('make_model')} ({x.get('recovery_status')})" for x in c["stolen_vehicles"]))
    if c.get("cyber_identifiers"):
        cy = c["cyber_identifiers"]
        lines.append(f"- Cyber trail: Rs {_fmt_int(cy.get('fraud_amount'))} from {cy.get('victim_count')} victims; "
                     f"UPI {cy.get('upi_ids')}; phones {cy.get('phone_numbers')}")
        for r in c.get("cyber_rings") or []:
            lines.append(f"- Part of fraud ring {r['identifier']} spanning {r['linked_cases']} cases (Rs {_fmt_int(r['total_fraud'])})")
    if c.get("cctv"):
        lines.append(f"- CCTV: {len(c['cctv'])} camera hit(s), e.g. {c['cctv'][0].get('camera_id')}")
    if c.get("anomaly_flag"):
        lines.append("- Flagged anomalous: " + ", ".join(c["anomaly_flag"]))
    if c.get("narrative"):
        lines.append(f"- Narrative: “{c['narrative'][:420]}{'…' if len(c['narrative']) > 420 else ''}”")
    if c.get("similar_cases"):
        lines.append("- Similar MO: " + "; ".join(f"{s['case_id']} ({s['district']}, {s['status']}; shares {', '.join(s['shared_mo']) or 'crime type'})" for s in c["similar_cases"]))
    return "\n".join(lines)


def _say_person(p):
    if p.get("error"):
        return p["error"]
    if p.get("ambiguous"):
        cands = p.get("candidates") or []
        return (f"{len(cands)}+ registry entries share the name “{p.get('query')}”. Which one do you mean?\n"
                + "\n".join(f"- {c['suspect_id']}: {c.get('risk_level')} risk, {c.get('prior_firs', 0)} priors"
                            + (f", {c.get('gang')}" if c.get("gang") else "") for c in cands[:6]))
    if "offender_record" in p:
        bits = []
        for o in p.get("other_matches") or []:
            bits.append(f"{o['kind']} {o['id']}")
        if p.get("victim_case_count"):
            bits.append(f"victim in {p['victim_case_count']} case(s), e.g. {', '.join(p['named_as_victim_in'][:3])}")
        return f"“{p.get('query')}” is not in the offender registry. Matches: " + "; ".join(bits) + "."
    flags = [f for f, on in (("rowdy-sheeter", p.get("rowdy_sheeter")), ("history-sheeter", p.get("history_sheeter")),
                             ("ACTIVE WARRANT", p.get("active_warrant"))) if on]
    head = (f"**{p.get('name')} ({p['suspect_id']})**" + (f", alias {p['alias']}" if p.get("alias") else "")
            + (f", age {p['age']}" if p.get("age") else "") + f" — {p.get('risk_level') or 'unrated'} risk"
            + (f", {p['gang']}" if p.get("gang") else ", no gang on file") + ".")
    lines = [head]
    lines.append(f"- Registry: {p.get('prior_firs', 0)} prior FIRs, {p.get('past_arrests', 0)} arrests"
                 + (" · " + ", ".join(flags) if flags else " · no rowdy/history sheet, no active warrant"))
    if p.get("cases_in_corpus"):
        lines.append(f"- Named in **{p['cases_in_corpus']} case(s)** between {p.get('first_seen')} and {p.get('last_seen')}: "
                     + _kv(p.get("crime_types"), 4))
        lines.append(f"- Districts: {_kv(p.get('districts'), 4)} · status: {_kv(p.get('case_status'), 3)}"
                     + (f" · court: {_kv(p.get('court_outcomes'), 3)}" if p.get("court_outcomes") else ""))
        lines.append("- Latest: " + "; ".join(f"{c['case_id']} {c['crime_type']} ({c['district']}, {c['registered']}, {c['role']}, {c['status']})" for c in p["cases"][:3]))
    if p.get("co_offenders"):
        lines.append("- Co-offenders: " + "; ".join(f"{c.get('name')} ({c['suspect_id']}, {c['shared_cases']} shared)" for c in p["co_offenders"][:5]))
    r = p.get("recidivism")
    if r:
        lines.append(f"- Recidivism index {r.get('index')}/100" + (f" ({r['band']})" if r.get("band") else "")
                     + (f" — drivers: {', '.join(r['drivers'])}" if r.get("drivers") else "")
                     + ". This orders attention; it is not evidence.")
    for s in p.get("syndicates") or []:
        lines.append(f"- Member of syndicate {s['syndicate_id']} ({s['size']} people, {s['risk_level']} risk)")
    if p.get("predicted_links"):
        lines.append("- Predicted emerging link(s): " + ", ".join(f"{l['with']} (score {l['score']})" for l in p["predicted_links"]))
    return "\n".join(lines)


def _say_cases(r):
    ap = r.get("filters_applied") or {}
    desc = " ".join(str(ap[k]) for k in ("crime_type", "status") if k in ap) or "FIRs"
    where = (f" in {ap['district']}" if ap.get("district") else "") + (f" at {ap['police_station']}" if ap.get("police_station") else "") \
        + (f" during {ap['year']}" if ap.get("year") else "") + (" involving deaths" if ap.get("has_fatalities") else "")
    n = r.get("total_matching", 0)
    if not n:
        return f"No {desc}{where} are recorded in the corpus."
    lines = [f"**{_fmt_int(n)} {desc}{where}** — {r.get('share_of_corpus_pct')}% of the corpus"
             + (f", {r['date_range'][0]} to {r['date_range'][1]}" if r.get("date_range") else "") + "."]
    lines.append(f"- Status: {_kv(r.get('by_status'))}")
    if r.get("by_crime_type"):
        lines.append(f"- Crime types: {_kv(r.get('by_crime_type'))}")
    if r.get("by_district"):
        lines.append(f"- Districts: {_kv(r.get('by_district'))}")
    lines.append(f"- {_fmt_int(r.get('fatalities'))} killed, {_fmt_int(r.get('injured'))} injured · average severity {r.get('avg_severity')}")
    label = {"severity": "Most severe", "fatalities": "Deadliest", "oldest": "Oldest"}.get(r.get("sorted_by"), "Most recent")
    lines.append(f"- {label}: " + "; ".join(f"{c['case_id']} ({c['crime_type']}, {c['district']}, {c['registered']}, {c['status']})" for c in (r.get("cases") or [])[:4]))
    return "\n".join(lines)


def _say_aggregate(r):
    g = r.get("group_by", "").replace("_", " ")
    ap = r.get("filters_applied") or {}
    scope = " ".join(str(v) for v in ap.values()) or "all FIRs"
    groups = r.get("groups") or {}
    if not groups:
        return f"No records match {scope}."
    metric = {"count": "cases", "fatalities": "deaths", "injured": "injured", "avg_severity": "avg severity"}[r.get("metric", "count")]
    items = list(groups.items())
    top = items[0]
    lines = [f"By {g} ({scope}, {_fmt_int(r.get('total_cases'))} cases): **{top[0]}** leads with {_fmt_int(top[1]) if metric != 'avg severity' else top[1]} {metric}."
             if not r.get("chronological") else f"{metric.capitalize()} by {g} ({scope}):"]
    lines.append("- " + "\n- ".join(f"{k}: {_fmt_int(v) if metric != 'avg severity' else v}" for k, v in items[:12]))
    if r.get("n_groups", 0) > 12 and not r.get("chronological"):
        lines.append(f"({r['n_groups']} {g} values in total)")
    if g == "district" and metric == "cases":
        lines.append("Recorded volume reflects reporting and enforcement as much as underlying crime.")
    return "\n".join(lines)


def _say_people(r):
    ap = r.get("filters_applied") or {}
    n = r.get("total_matching", 0)
    what = ", ".join(f"{k.replace('_', ' ')}={v}" for k, v in ap.items()) or "all registered offenders"
    if not n:
        return f"No offenders match {what}."
    lines = [f"**{_fmt_int(n)} offenders** match {what}."]
    lines.append(f"- Risk levels: {_kv(r.get('by_risk_level'))}")
    lines.append(f"- {_fmt_int(r.get('rowdy_sheeters'))} rowdy-sheeters, {_fmt_int(r.get('history_sheeters'))} history-sheeters, "
                 f"{_fmt_int(r.get('with_active_warrant'))} with active warrants")
    if r.get("top_gangs"):
        lines.append(f"- Gangs: {_kv(r.get('top_gangs'))}")
    if r.get("people"):
        lines.append("- Most priors: " + "; ".join(f"{p.get('name')} ({p['suspect_id']}, {p.get('prior_firs')} priors)" for p in r["people"][:5]))
    return "\n".join(lines)


def _say_officer(o):
    if o.get("error"):
        return o["error"]
    if o.get("ambiguous"):
        return "Several officers share that name: " + "; ".join(f"{c['officer_id']} {c.get('rank')} at {c.get('station')}" for c in o["candidates"])
    lines = [f"**{o.get('name')} ({o['officer_id']})** — {o.get('rank')}, {o.get('station')}, {o.get('district')}; "
             f"specialises in {o.get('specialization')}."]
    cr = o.get("closure_rate")
    lines.append(f"- {o.get('cases_assigned')} cases assigned, closure rate {round(cr * 100) if isinstance(cr, (int, float)) else cr}%, "
                 f"{o.get('commendations')} commendations; appointed {o.get('appointment_date')}")
    if o.get("cases_as_io_in_corpus"):
        lines.append(f"- IO on {o['cases_as_io_in_corpus']} FIRs in the corpus: {_kv(o.get('by_status'))}")
        lines.append("- Recent: " + "; ".join(f"{c['case_id']} {c['crime_type']} ({c['status']})" for c in o.get("recent_cases", [])[:3]))
    return "\n".join(lines)


def _say_officers(r):
    offs = r.get("officers") or []
    if not offs:
        return "No officers match."
    return (f"**{r['total_matching']} officers** (ranked by {r['sorted_by'].replace('_', ' ')}):\n"
            + "\n".join(f"- {o.get('name')} ({o.get('officer_id')}), {o.get('rank')}, {o.get('station')}: "
                        f"{round((o.get('closure_rate') or 0) * 100)}% closure, {o.get('cases_assigned')} cases"
                        for o in offs[:8]))


def _say_registry(r):
    if r.get("error"):
        return r["error"]
    kind = r["registry"]
    n = r["total_matching"]
    label = {"vehicles": "stolen-vehicle", "cybercrime": "cyber-fraud", "missing": "missing-person",
             "courts": "court", "complaints": "Dial-112 call", "cctv": "CCTV"}.get(kind, kind)
    scope = ", ".join(str(v) for v in (r.get("query"), r.get("district"), r.get("status")) if v)
    if not n:
        return f"No {label} records match {scope or 'that'}."
    if kind == "cybercrime" and r.get("total_fraud_rs") is not None:
        head = (f"**Rs {_fmt_int(r['total_fraud_rs'])} lost** to {_fmt_int(r['total_victims'])} victims across "
                f"{_fmt_int(n)} cyber-fraud case(s)" + (f" matching {scope}" if scope else "") + ".")
    elif scope:
        head = f"**{_fmt_int(n)} of {_fmt_int(r['total_records'])} {label} records** match {scope}."
    else:
        head = f"The {label} registry holds **{_fmt_int(n)} records**."
    lines = [head]
    for k, v in r.items():
        if k.startswith("by_"):
            lines.append(f"- {k[3:].replace('_', ' ').capitalize()}: {_kv(v)}")
    if r.get("avg_response_min") is not None:
        lines.append(f"- Average response time {r['avg_response_min']} min")
    for rec in (r.get("records") or [])[:5 if r.get("query") else 3]:
        lines.append("- " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in rec.items() if v not in (None, "") and k != "owner_name"))
    return "\n".join(lines)


def _say_network(r):
    if r.get("error"):
        return r["error"]
    if "connected" in r:
        if not r["connected"]:
            return f"{r['from']} and {r['to']} are not connected in the link graph. {r.get('note', '')}"
        hops = " → ".join(f"{n.get('name') or n['id']}" + (f" [{n['id']}]" if n.get("name") else f" ({n.get('crime_type') or n['type']})") for n in r["path"])
        return f"**Connected in {r['hops']} hop(s):** {hops}"
    e = r["entity"]
    return (f"**{e.get('name') or e['id']}** has {r['degree']} direct link(s): {_kv(r['neighbours_by_type'])}"
            + (f"; {r['second_degree_suspects']} suspects at second degree." if "second_degree_suspects" in r else "."))


def _say_insight(topic, r):
    if r.get("error"):
        return r["error"]
    if topic == "forecast":
        s = r.get("statewide") or {}
        out = [f"**Forecast:** about **{s.get('next_week')} incidents statewide next week** ({s.get('trend')}, "
               f"{s.get('change_pct'):+}% vs the prior 4 weeks; average {s.get('avg_weekly')}/week)." if s.get("change_pct") is not None else "Forecast unavailable."]
        if r.get("district"):
            for d, v in r["district"].items():
                out.append(f"- {d}: ~{v.get('next_week')} next week ({v.get('trend')}, {v.get('change_pct'):+}%)")
        if r.get("crime_type"):
            for d, v in r["crime_type"].items():
                out.append(f"- {d}: ~{v.get('next_week')} next week ({v.get('trend')}, {v.get('change_pct'):+}%)")
        if r.get("districts_rising"):
            out.append("- Rising: " + ", ".join(f"{d} ({c:+}%)" for d, c in r["districts_rising"].items()))
        out.append("The model beats naive baselines but not a 4-week moving average — read it as a range, not a number.")
        return "\n".join(out)
    if topic == "hotspots":
        z = r.get("zones") or []
        if r.get("district_filter"):
            if not z:
                return f"No DBSCAN hotspot zone is centred in {r['district_filter']}; incidents there are dispersed."
            return (f"**{len(z)} hotspot zone(s) in {r['district_filter']}** (of {r.get('dbscan_zones')} statewide):\n"
                    + "\n".join(f"- ~{c['radius_km']} km zone: {c['cases']} incidents, mostly {c['dominant_crime']}, "
                                f"{c['open_cases']} open, peak {c['peak_time']}, avg severity {c['avg_severity']}" for c in z[:5]))
        return (f"**{r.get('dbscan_zones')} DBSCAN hotspot zones** detected.\n"
                + "\n".join(f"- {c['district']}: {c['cases']} incidents within ~{c['radius_km']} km, mostly {c['dominant_crime']}, "
                            f"{c['open_cases']} open, peak {c['peak_time']}" for c in z[:5]))
    if topic == "trends":
        al = r.get("alerts") or []
        if not al:
            return "No crime category is currently spiking above its historical baseline."
        return ("**Emerging trend alerts** (last 30 days vs baseline):\n"
                + "\n".join(f"- {a['crime_type']} in {a['district']}: {a['recent_count']} cases vs {a['expected_count']} expected ({a['spike_ratio']}×)" for a in al[:6])
                + "\nA spike can reflect reporting changes as easily as offending.")
    if topic == "anomalies":
        an = r.get("anomalies") or []
        return f"**{r.get('count', 0)} anomalous incidents** flagged by Isolation Forest." + (
            "\n" + "\n".join(f"- {a.get('case_id')}: {a.get('crime_type')} in {a.get('district')} — {', '.join(a.get('reasons') or [])}" for a in an[:5]) if an else "")
    if topic == "recidivism":
        b = r.get("bands") or {}
        return (f"**Recidivism bands:** {_kv(b)}.\n"
                + "- Highest indices (ids only — names stay in the gated registry): "
                + "; ".join(f"{t['criminal_id']} {t['risk_index']}" for t in (r.get("top_by_id") or [])[:5])
                + "\nThe index is a percentile for ordering attention; it is not a prediction about any person.")
    if topic == "pendency":
        s = r.get("summary") or {}
        bd = r.get("by_district") or []
        if len(bd) == 1:
            d = bd[0]
            out = [f"**{d['name']}: {d['open_cases']} open FIRs** without chargesheet, {d['breached_90d']} of them past 90 days "
                   f"({round(d['breach_rate_90d'] * 100)}%), mean {d['mean_days_open']} days open. Statewide: "
                   f"{_fmt_int(s.get('open_cases'))} open, {_fmt_int(s.get('already_breached_90d'))} past 90 days."]
            for c in (r.get("longest_expected_delay") or [])[:3]:
                out.append(f"- Likely to drag: {c['case_id']} ({c['crime_type']}), ~{round(c['expected_additional_days'] or 0)} more days expected")
            return "\n".join(out)
        out = [f"**{_fmt_int(s.get('open_cases'))} open FIRs** without chargesheet; {_fmt_int(s.get('already_breached_90d'))} already past 90 days "
               f"(median open {s.get('median_days_open')} days). {_fmt_int(s.get('backlog'))} sit beyond the model's {s.get('grid_max_days')}-day grid and are reported as backlog, not scored."]
        for d in (r.get("by_district") or [])[:5]:
            out.append(f"- {d['name']}: {d['open_cases']} open, {d['breached_90d']} past 90 days")
        for c in (r.get("longest_expected_delay") or [])[:3]:
            out.append(f"- Likely to drag: {c['case_id']} ({c['crime_type']}, {c['district']}), ~{round(c['expected_additional_days'] or 0)} more days")
        return "\n".join(out)
    if topic == "dispatch":
        return (f"**Patrol plan:** {_fmt_int(r.get('total_recommended_officers'))} officers recommended statewide.\n"
                + "\n".join(f"- {d['district']}: risk {d['risk_score']} ({d['risk_level']}) → {d['recommended_officers']} officers; {d['open_cases']} open cases" for d in (r.get("districts") or [])[:6]))
    if topic == "socioeconomic":
        out = ["**Socio-economic correlation:**"] + [f"- {i}" for i in (r.get("insights") or [])[:5]]
        if r.get("district"):
            d = r["district"]
            out.append(f"- {d.get('district')}: " + ", ".join(f"{k} {v}" for k, v in d.items() if k != "district" and not isinstance(v, (dict, list)))[:300])
        out.append("These are correlations across 31 districts, not causes.")
        return "\n".join(out)
    if topic == "operational_index":
        return ("**District operational risk index:**\n"
                + "\n".join(f"- {d['district']}: {d['index']} ({d['band']})" for d in (r.get("districts") or [])[:8]))
    if topic == "lifecycle":
        st = ((r.get("cohort") or {}).get("stages")) or []
        return ("**Case lifecycle** (cohort " + str((r.get("cohort") or {}).get("label")) + "):\n"
                + "\n".join(f"- {s['stage']}: {_fmt_int(s['count'])} ({round(s['share'] * 100, 1)}%)" for s in st))
    if topic == "predicted_links":
        return ("**Predicted emerging alliances** (Adamic-Adar):\n"
                + "\n".join(f"- {l['a_name']} ({l['a']}) ↔ {l['b_name']} ({l['b']}): {l['common_associates']} shared associates, score {l['score']}" for l in (r.get("links") or [])[:6]))
    if topic == "syndicates":
        return (f"**{r.get('count')} syndicates** detected by repeat co-offending. Largest:\n"
                + "\n".join(f"- {s['syndicate_id']}: {s['size']} members, {s['risk_level']} risk ({', '.join(s['member_ids'][:5])}…)" for s in (r.get("largest") or [])[:5]))
    if topic == "cyber_rings":
        return (f"**Cyber fraud:** {_fmt_int(r.get('cyber_cases'))} cases, Rs {_fmt_int(r.get('total_fraud_rs'))} lost by {_fmt_int(r.get('victims'))} victims.\n"
                + "\n".join(f"- {x['identifier']} links {x['linked_cases']} cases (Rs {_fmt_int(x['total_fraud'])})" for x in (r.get("rings") or [])[:5]))
    if topic == "courts":
        return (f"**Courts:** {_fmt_int(r.get('court_records'))} trial records — {_kv(r.get('by_outcome'))}. "
                f"Conviction rate among decided trials: **{r.get('conviction_rate_of_decided_pct')}%**.\n"
                f"- Busiest courts: {_kv(r.get('by_court'), 4)}")
    # overview
    return (f"**Corpus overview:** {_fmt_int(r.get('total_firs'))} FIRs"
            + (f" ({r['date_range'][0]} to {r['date_range'][1]})" if r.get("date_range") else "")
            + f", {_fmt_int(r.get('offenders'))} offenders.\n"
            f"- Status: {_kv(r.get('by_status'))}\n- Top crimes: {_kv(r.get('top_crime_types'))}\n"
            f"- Top districts: {_kv(r.get('top_districts'))}\n"
            f"- {_fmt_int(r.get('fatalities'))} killed, {_fmt_int(r.get('injured'))} injured · "
            f"{_fmt_int(r.get('rowdy_sheeters'))} rowdy-sheeters, {_fmt_int(r.get('active_warrants'))} active warrants · "
            f"{_fmt_int(r.get('stolen_vehicles'))} stolen vehicles ({r.get('vehicle_recovery_rate_pct')}% recovered)")


def _say_model(r):
    if r.get("error"):
        return r["error"]
    if "models" in r:
        return ("**Model registry** — each scored on a later, held-out time window against a trivial baseline:\n"
                + "\n".join(f"- {n}: {m.get('headline') or ', '.join(f'{k} {v}' for k, v in m['key_metrics'].items())}"
                            for n, m in r["models"].items()))
    mt = r.get("metrics") or {}
    keys = [k for k in ("accuracy", "roc_auc", "pr_auc", "brier", "f1_macro", "mase", "c_index",
                        "integrated_brier", "silhouette", "n_test") if k in mt]
    out = [f"**{r['model']}** — {r.get('headline') or ''}".rstrip(" —"),
           "- Metrics: " + ", ".join(f"{k} {mt[k]}" for k in keys)]
    bl = r.get("baselines") or []
    if bl:
        out.append("- Baselines: " + "; ".join(
            f"{b.get('name') or b.get('strategy')}: " + ", ".join(f"{k} {v}" for k, v in b.items()
                                                                   if isinstance(v, (int, float)))[:120] for b in bl[:3]))
    for n in (r.get("notes") or [])[:2]:
        out.append(f"- Note: {n}")
    return "\n".join(out)


def _say_knowledge(r):
    secs = r.get("sections") or []
    if secs and secs[0].get("excerpt"):
        return secs[0]["excerpt"]
    return "\n\n".join(f"**{s['title']}** — {s['text']}" for s in secs)


def compose(calls_and_results):
    parts = []
    for (name, args), res in calls_and_results:
        if name == "get_case":
            parts.append(_say_case(res))
        elif name == "get_person":
            parts.append(_say_person(res))
        elif name == "search_cases":
            parts.append(_say_cases(res))
        elif name == "aggregate":
            parts.append(_say_aggregate(res))
        elif name == "search_people":
            parts.append(_say_people(res))
        elif name == "get_officer":
            parts.append(_say_officer(res))
        elif name == "search_officers":
            parts.append(_say_officers(res))
        elif name == "search_registry":
            parts.append(_say_registry(res))
        elif name == "network":
            parts.append(_say_network(res))
        elif name == "insight":
            parts.append(_say_insight(args.get("topic"), res))
        elif name == "project_knowledge":
            parts.append(_say_knowledge(res))
        elif name == "model_info":
            parts.append(_say_model(res))
    return "\n\n".join(p for p in parts if p)


def _focused_answer(text, calls, results):
    """Pull the one fact a narrow question asks about out of a full dossier."""
    low = (text or "").lower()
    if len(results) != 1:
        return None
    (name, _args), res = results[0]
    if name == "get_case" and not res.get("error"):
        cid = res["case_id"]
        acc = res.get("accused") or []
        if _cue(low, ("accused", "suspect", "who did", "who committed", "culprit", "involved", "offender")):
            if not acc:
                return f"No accused are linked to FIR {cid} in the corpus."
            return (f"FIR {cid} ({res.get('crime_type')}, {res.get('district')}) names {len(acc)} accused:\n"
                    + "\n".join(f"- {a.get('name')} ({a['suspect_id']}) — {a.get('relationship')}, {a.get('risk_level')} risk, "
                                f"{a.get('prior_firs', 0)} priors" + (", rowdy-sheeter" if a.get("rowdy_sheeter") else "")
                                + (", active warrant" if a.get("active_warrant") else "") for a in acc))
        if _cue(low, ("victim",)):
            v = res.get("victim") or {}
            return f"The victim in FIR {cid} is {v.get('name') or 'not recorded'}" + (f", aged {v.get('age')}" if v.get("age") else "") + (f" ({v.get('gender')})" if v.get("gender") else "") + "."
        if _cue(low, ("officer", "io ", "investigat", "who is handling", "handled")):
            io = res.get("investigating_officer") or {}
            return f"FIR {cid} is investigated by {io.get('name') or 'an officer'} ({io.get('officer_id')}, {io.get('rank') or 'rank n/a'}, {io.get('station') or res.get('police_station')})."
        if _cue(low, ("court", "trial", "hearing", "convict", "verdict")):
            c = res.get("court")
            if not c:
                return f"FIR {cid} has no court record yet — status is {res.get('status')}."
            return (f"FIR {cid} is in {c.get('court_name')} as {c.get('court_case_no')}: {c.get('conviction_status')}"
                    + (f", next hearing {c.get('next_hearing')}" if c.get("conviction_status") == "Pending Trial" else "") + ".")
        if _cue(low, ("status", "solved", "closed", "chargesheet", "progress")):
            cs = res.get("chargesheet")
            return (f"FIR {cid} is **{res.get('status')}**"
                    + (f"; chargesheet filed {cs.get('filed_on')}" if cs else "; no chargesheet recorded")
                    + f"; {res.get('arrests', {}).get('count', 0)} arrest(s).")
        if _cue(low, ("narrative", "what happened", "brief facts", "story", "describe")):
            return f"FIR {cid} narrative: “{res.get('narrative') or 'not recorded'}”"
        if _cue(low, ("similar", "related", "same mo", "pattern", "like this")):
            sc = res.get("similar_cases") or []
            return f"Cases with a similar MO to FIR {cid}:\n" + "\n".join(f"- {s['case_id']} ({s['district']}, {s['registered']}, {s['status']}) — shares {', '.join(s['shared_mo']) or 'crime type'}" for s in sc)
    if name == "get_person" and not res.get("error") and not res.get("ambiguous") and "offender_record" not in res:
        sid = res["suspect_id"]
        nm = res.get("name")
        if _cue(low, ("warrant",)):
            return f"{nm} ({sid}) {'HAS an active warrant' if res.get('active_warrant') else 'has no active warrant'} on file."
        if _cue(low, ("rowdy",)):
            return f"{nm} ({sid}) {'is' if res.get('rowdy_sheeter') else 'is not'} on the rowdy register."
        if _cue(low, ("associate", "co-offender", "partner", "accomplice", "works with", "network")):
            co = res.get("co_offenders") or []
            return (f"{nm} ({sid}) has {len(co)} co-offender(s) across shared cases:\n"
                    + "\n".join(f"- {c.get('name')} ({c['suspect_id']}): {c['shared_cases']} shared case(s), {c.get('risk_level')} risk" for c in co)
                    if co else f"{nm} ({sid}) shares no case with another offender.")
        if _cue(low, ("how many case", "cases", "history", "record", "crimes")) and res.get("cases"):
            return _say_person(res)
    return None


def offline_answer(text, cache, context=None, history=None):
    """Answer from tools alone. Returns dict or None when no plan applies."""
    idx = _index(cache)
    if idx is None:
        return None
    ents = extract_entities(text, idx, context, history)
    calls = plan(text, idx, ents)
    if not calls:
        return None
    results = [((n, a), run_tool(n, a, cache)) for n, a in calls]
    reply = _focused_answer(text, calls, results) or compose(results)
    if not reply:
        return None
    return {"reply": reply, "trace": [_trace(n, a, r) for (n, a), r in results],
            "entities": ents, "nav": _default_nav(results), "mode": "deterministic"}


def _default_nav(results):
    """A screen worth showing next to the answer, or None."""
    for (name, _a), res in results:
        if name == "get_case" and res.get("case_id"):
            return {"action": "open_case", "params": {"case_id": res["case_id"]}}
        if name == "get_person" and res.get("suspect_id"):
            return {"action": "open_suspect", "params": {"suspect_id": res["suspect_id"]}}
    return None


def _trace(name, args, res):
    label = {"get_case": "Read case dossier", "get_person": "Read person profile",
             "search_cases": "Searched FIRs", "aggregate": "Ranked FIRs", "search_people": "Searched offender registry",
             "get_officer": "Read officer record", "search_officers": "Searched officers",
             "search_registry": "Searched registry", "network": "Traversed link graph",
             "insight": "Read analytics", "model_info": "Read model registry",
             "project_knowledge": "Read platform docs", "navigate": "Moved screen"}.get(name, name)
    detail = ", ".join(f"{v}" for k, v in (args or {}).items() if v not in (None, "", False) and k not in ("limit", "sort_by", "order"))
    n = None
    if isinstance(res, dict):
        n = res.get("total_matching", res.get("cases_in_corpus"))
    return {"tool": name, "label": label, "detail": detail[:80],
            "result": (f"{n:,} match" if isinstance(n, int) else ("error" if res.get("error") else "ok")) if isinstance(res, dict) else "ok"}


# =============================================================================
# LLM tool-calling loop
# =============================================================================
def _system_prompt(lang_directive, briefing, page, context):
    here = ""
    if context:
        if context.get("case_id"):
            here = f"\nThe officer is currently viewing case {context['case_id']}; 'this case', 'it' refer to it."
        elif context.get("suspect_id"):
            here = f"\nThe officer is currently viewing offender {context['suspect_id']}; 'he', 'she', 'this person' refer to them."
    return (
        f"{lang_directive}\n\n"
        "You are Kavya, the investigative copilot of KSP IntelHub — the AI crime-intelligence platform of the "
        "Karnataka State Police State Crime Records Bureau (SCRB). You think like a senior crime analyst: precise, "
        "proactive, and honest about uncertainty.\n\n"
        "HOW YOU WORK\n"
        "- You have tools that read the live corpus: 18,000 FIRs, 9,000 offenders, officers, courts, vehicles, cyber "
        "identifiers, missing persons, the link graph, every ML output and the platform documentation. USE THEM. "
        "Never answer a factual question about a case, person, count, ranking or trend from memory — call a tool.\n"
        "- Records the question names may already be PRE-FETCHED below; use them instead of re-fetching.\n"
        "- Chain tools when needed: e.g. get_case → get_person on its accused → network between two people.\n"
        "- Person names appear as [PERSON_n] aliases. Use the alias exactly as given; it is turned back into the name "
        "for the officer. Never invent a name.\n"
        "- Only state case numbers, SUS-ids and KGIDs that appear in tool results. If a tool returns nothing, say so.\n\n"
        "HOW YOU ANSWER\n"
        "- Lead with the direct answer in one sentence, with the exact number or fact. Then 2-6 short bullet points "
        "of supporting detail and, where useful, one concrete investigative next step (a lead, a link to check, a "
        "pattern to watch). Use **bold** for the key figure. Keep it under ~180 words unless asked for depth.\n"
        "- Be conversational and natural; no filler, no repeating the question, no 'As an AI'.\n"
        "- If the question is ambiguous (e.g. a name shared by several people), say what you found and ask which one.\n"
        "- Call the navigate tool when opening a screen helps (a single case or person → open it).\n"
        "- End with one line: FOLLOWUPS: <short follow-up question> | <another> | <another>\n\n"
        "LIMITS YOU KEEP\n"
        "- No output is evidence. Never say a person is guilty, will reoffend, or should be arrested; a risk index "
        "orders attention, it does not establish anything about a person.\n"
        "- The corpus is simulated CCTNS-shaped data; say so if asked whether the records are real.\n"
        "- Outside policing/this platform, answer briefly and steer back.\n"
        f"{here}\n\nCURRENT PAGE: {page or '/'}\n\nLIVE BRIEFING (statewide aggregates):\n{briefing}"
    )


def _parse_followups(text):
    if not text:
        return text, []
    m = re.search(r"\n?\s*\**FOLLOW-?UPS?\**\s*:\s*(.+)$", text, re.I | re.S)
    if not m:
        return text.strip(), []
    ups = [u.strip(" -•*\"'") for u in re.split(r"\s*\|\s*|\n", m.group(1)) if u.strip(" -•*\"'")]
    return text[:m.start()].strip(), ups[:3]


def _chat(providers, messages, tools):
    """One completion against the first provider that answers. Returns (message, provider)."""
    try:
        from openai import OpenAI
    except Exception as e:
        LOG.warning("openai client unavailable: %s", e)
        return None, None
    for p in providers:
        key = os.environ.get(p["key_env"], "")
        if not key or key.startswith(("nvapi-****", "gsk_****")):
            continue
        try:
            client = OpenAI(base_url=p["base_url"], api_key=key, timeout=LLM_TIMEOUT_S, max_retries=1)
            kwargs = dict(model=os.environ.get(p["model_env"], p["model_default"]), messages=messages,
                          temperature=0.2, max_tokens=900)
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message, p["name"]
        except Exception as e:
            LOG.warning("copilot provider %s failed: %s", p["name"], e)
            continue
    return None, None


def llm_answer(text, cache, providers, history=None, lang_directive="", briefing="", page=None,
               context=None, extra_facts=None):
    """Tool-calling answer. Returns dict or None if no provider produced one."""
    idx = _index(cache)
    if idx is None:
        return None
    ents = extract_entities(text, idx, context, history)
    pre_calls = plan(text, idx, ents)[:4]
    pre = [((n, a), run_tool(n, a, cache)) for n, a in pre_calls]
    trace = [_trace(n, a, r) for (n, a), r in pre]

    user = text
    blocks = []
    if ents.get("resolved_from_context"):
        blocks.append(f"(pronoun in the question refers to {ents['resolved_from_context']})")
    if pre:
        blocks.append("PRE-FETCHED RECORDS:\n" + "\n".join(
            f"[{n}({_dump(a, 200)})] → {_dump(mask_for_llm(r), 5000)}" for (n, a), r in pre))
    if extra_facts:
        blocks.append("VERIFIED PLATFORM FACT: " + extra_facts)
    if blocks:
        user = f"{text}\n\n" + "\n\n".join(blocks)

    hist = []
    for m in (history or [])[-8:]:
        role, content = m.get("role"), (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            hist.append({"role": role, "content": privacy_mask_text(content, idx)[:1500]})
    messages = [{"role": "system", "content": _system_prompt(lang_directive, briefing, page, context)}] + hist + \
               [{"role": "user", "content": user}]
    tools = tool_specs()
    nav = None
    provider = None
    for step in range(MAX_TOOL_STEPS + 1):
        msg, provider = _chat(providers, messages, tools if step < MAX_TOOL_STEPS else None)
        if msg is None and step > 0:
            # A provider that rejects a malformed tool call mid-loop still has the
            # evidence gathered so far in context; ask once more for a plain answer.
            msg, provider = _chat(providers, _flatten(messages), None)
        if msg is None:
            return None
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            content = (msg.content or "").strip()
            if not content:
                return None
            reply, ups = _parse_followups(privacy.restore(content))
            return {"reply": reply, "followups": [privacy.restore(u) for u in ups], "trace": trace,
                    "nav": nav or _default_nav(pre), "entities": ents, "mode": f"llm:{provider}"}
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                        for c in calls]})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except ValueError:
                args = {}
            args = {k: privacy.restore(v) if isinstance(v, str) else v for k, v in (args or {}).items()}
            if c.function.name == "navigate":
                act = args.pop("action", None)
                if act in NAV_ACTIONS:
                    nav = {"action": act, "params": {k: v for k, v in args.items() if v}}
                result = {"ok": True}
            else:
                result = run_tool(c.function.name, args, cache)
            trace.append(_trace(c.function.name, args, result))
            messages.append({"role": "tool", "tool_call_id": c.id,
                             "content": _dump(mask_for_llm(result), 6000)})
    return None


def _flatten(messages):
    """The same conversation with tool turns folded into text, for a tool-less retry."""
    out, results = [], []
    for m in messages:
        if m.get("role") == "tool":
            results.append(m["content"])
        elif m.get("tool_calls"):
            continue
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    if results:
        out.append({"role": "user", "content": "TOOL RESULTS GATHERED SO FAR:\n" + "\n".join(results)[:12000]
                    + "\n\nAnswer the question above from these results."})
    return out


def privacy_mask_text(text, idx):
    """Alias registry names appearing in conversation history before it is re-sent."""
    if not text:
        return text
    for name, _hits in find_names(text, idx):
        text = re.sub(re.escape(name), privacy.mask(name), text, flags=re.I)
    return text


def speech_summary(reply, limit=260):
    """A short spoken version: the first sentence or two, without markup or long ids."""
    t = re.sub(r"\*\*|__|`|#+\s*", "", reply or "").replace("→", ", then").replace("·", ",")
    lines = [ln.strip(" -•") for ln in t.split("\n") if ln.strip(" -•")]
    t = lines[0] if lines else t.strip()
    if t.endswith(":") and len(lines) > 1:
        t = f"{t} {lines[1]}."
    sentences = re.split(r"(?<=[.!?])\s+", t)
    out = ""
    for s in sentences:
        if len(out) + len(s) > limit and out:
            break
        out = (out + " " + s).strip()
    return out[:limit]
