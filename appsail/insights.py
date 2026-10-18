"""Two aggregate views over the FIR corpus: who is involved, and where cases end up.

* ``GET /serverless/api/analytics/demographics`` — the socio-economic profile of the
  people on the FIRs in scope: complainant occupation, complainant / victim /
  accused age bands, and gender. The analytics page draws its "By occupation"
  chart from this.
* ``GET /serverless/api/lifecycle/flow`` — a three-column case flow (crime type ->
  FIR status -> outcome) for the lifecycle page's Sankey.

Both are open, aggregate-only endpoints. Nothing here returns a name, a person id,
a case number or a row; the tests assert that against the real name columns.

Deliberately left out
---------------------
* **Caste and religion.** `ComplainantDetails` carries both columns. They are never
  read here: the frame is cut to the four fields this module needs before anything
  else touches it. A district-by-caste breakdown of complainants is the chart a
  police analytics platform should not make easy, whatever it shows.
* **Small scopes.** A district + crime-type filter narrows the corpus, and the
  endpoint is open. Below `MIN_SCOPE_FIRS` FIRs the breakdowns are withheld rather
  than published, so a filter cannot be walked down to a handful of identifiable
  people. On the current corpus no district x crime-type cell is that small; the
  rule exists for the live data this is meant to run on.

Honest about the corpus
-----------------------
The corpus is simulated (CCTNS-shaped, not KSP records), and some of its fields are
drawn uniformly at random. Complainant occupation is one: seven categories within a
fraction of a percentage point of each other. A chart of that looks like a finding
and is not one, so the demographics response runs a chi-square test against an even
split and says so in `note` when it cannot reject it.

The flow has a corpus problem of its own: the court registry records a verdict for
FIRs whose CaseMaster status is "Under Investigation" or "Closed" — a trial that
concluded on a file that was never charge-sheeted. The FIR status is taken as
authoritative and those court records are counted and reported, not used. The
lifecycle funnel (`analytics.build_case_lifecycle`) counts any recorded verdict, so
the two will not agree on conviction counts, and the flow's `note` says why.

Like `network_intel`, the data cache is only ever read through the getter given to
`configure`: main.py rebinds every table on a reload, and a reference captured at
import time would keep serving the previous corpus.
"""
import math
import threading

import pandas as pd
from flask import Blueprint, jsonify, request

bp = Blueprint("insights", __name__)
API = "/serverless/api"

_cache_getter = lambda: {}   # noqa: E731 — replaced by configure()


def configure(cache_getter):
    """Bind the live data cache. A callable, never the dict itself."""
    global _cache_getter
    _cache_getter = cache_getter


# --------------------------------------------------------------------- constants
# Below this many FIRs in scope the demographic breakdowns are withheld (see module
# docstring). A parameter, not a law; module-level so a test can move it.
MIN_SCOPE_FIRS = 10

# NCRB-style adult bands. One set for complainants, victims and accused so the three
# can be read side by side; an age outside 0-110 is not a plausible age and is
# reported as "Not recorded" rather than clipped into a band.
AGE_BANDS = (
    ("Under 18", 0, 17),
    ("18–29", 18, 29),
    ("30–44", 30, 44),
    ("45–59", 45, 59),
    ("60+", 60, 110),
)
NOT_RECORDED = "Not recorded"

# GenderID convention used by main.py when it builds `firs.victim_gender`.
GENDER = {1: "Male", 2: "Female", 3: "Transgender"}
GENDER_ORDER = ("Male", "Female", "Transgender")

STATUS_ORDER = ("Under Investigation", "Charge Sheeted", "Closed")
CHARGE_SHEETED = "Charge Sheeted"
VERDICT_ORDER = ("Convicted", "Acquitted", "Pending Trial")
AWAITING = "Awaiting court record"
STILL_UI = "Still under investigation"
CLOSED_OUTCOME = "Closed"
OTHER_STATUS = "Status not mapped"
UNCLASSIFIED = "Unclassified"

# Upper 5% points of the chi-square distribution, df 1-10. Larger df use the
# Wilson-Hilferty approximation, which is within 0.5% of the table from df 10 up.
_CHI2_CRIT_05 = (3.841, 5.991, 7.815, 9.488, 11.070, 12.592, 14.067, 15.507, 16.919, 18.307)


def _chi2_critical_05(df):
    if df < 1:
        return None
    if df <= len(_CHI2_CRIT_05):
        return _CHI2_CRIT_05[df - 1]
    z = 1.6449
    return df * (1 - 2 / (9 * df) + z * math.sqrt(2 / (9 * df))) ** 3


# ------------------------------------------------------------------ preparation
_SOURCE_KEYS = ("firs", "courts", "CaseMaster", "CrimeSubHead", "CrimeHead",
                "ComplainantDetails", "OccupationMaster", "Victim", "Accused")
_memo_lock = threading.Lock()
_memo = {"sources": None, "prepared": None}


def _prepared():
    """Joined, column-trimmed frames, built once per loaded corpus.

    Memoised on the *identity* of the source tables. A reload rebinds every key in
    the cache, so a changed object is a changed corpus; holding the old frames until
    the first request after a reload is the cost of not trusting `id()` reuse.
    """
    cache = _cache_getter() or {}
    sources = tuple(cache.get(k) for k in _SOURCE_KEYS)
    with _memo_lock:
        old = _memo["sources"]
        if old is not None and all(a is b for a, b in zip(old, sources)):
            return _memo["prepared"]
    prepared = _prepare(dict(zip(_SOURCE_KEYS, sources)))
    if prepared is not None:
        prepared["results"] = {}
    with _memo_lock:
        _memo["sources"] = sources
        _memo["prepared"] = prepared
    return prepared


def _frame(t, cols):
    """`t[cols]` when every column is present, else None — a missing optional table
    degrades its part of the response instead of taking the endpoint down."""
    if t is None or not isinstance(t, pd.DataFrame) or t.empty:
        return None
    if any(c not in t.columns for c in cols):
        return None
    return t[list(cols)].copy()


def _prepare(src):
    firs = _frame(src["firs"], ("CaseMasterID", "case_id", "district", "crime_type", "status"))
    if firs is None:
        return None
    firs["case_id"] = firs["case_id"].astype(str)

    # Crime head group, through the FIR's own minor-head id where CaseMaster is
    # available (exact), otherwise through the sub-head name (exact unless two
    # sub-heads share a name).
    sub = _frame(src["CrimeSubHead"], ("CrimeSubHeadID", "CrimeHeadID", "CrimeHeadName", "SeqID"))
    head = _frame(src["CrimeHead"], ("CrimeHeadID", "CrimeGroupName"))
    head_name = dict(zip(head["CrimeHeadID"], head["CrimeGroupName"])) if head is not None else {}
    head_ids = sorted(head_name)
    groups = [head_name[h] for h in head_ids]

    crime_meta = {}   # crime type -> {group, group_order, shade, shades}
    if sub is not None:
        sub = sub.sort_values(["CrimeHeadID", "SeqID", "CrimeSubHeadID"])
        for hid, rows in sub.groupby("CrimeHeadID", sort=True):
            names = list(dict.fromkeys(rows["CrimeHeadName"].astype(str)))
            g = head_name.get(hid, UNCLASSIFIED)
            for i, name in enumerate(names):
                crime_meta.setdefault(name, {
                    "group": g,
                    "group_order": head_ids.index(hid) if hid in head_name else len(head_ids),
                    "shade": i, "shades": len(names),
                })

    cm = _frame(src["CaseMaster"], ("CaseMasterID", "CrimeMinorHeadID"))
    if cm is not None and sub is not None:
        sub_to_head = dict(zip(sub["CrimeSubHeadID"], sub["CrimeHeadID"]))
        minor = firs["CaseMasterID"].map(dict(zip(cm["CaseMasterID"], cm["CrimeMinorHeadID"])))
        firs["head_group"] = minor.map(sub_to_head).map(head_name)
    else:
        firs["head_group"] = firs["crime_type"].map(
            {k: v["group"] for k, v in crime_meta.items()})
    firs["head_group"] = firs["head_group"].fillna(UNCLASSIFIED).astype(str)

    # One verdict per FIR. A duplicated court record keeps the last row and is
    # counted, so a registry problem shows up in the response instead of in a sum.
    courts = _frame(src["courts"], ("fir_number", "conviction_status"))
    duplicate_court_records = 0
    verdict = pd.Series(dtype=object)
    if courts is not None:
        courts["fir_number"] = courts["fir_number"].astype(str)
        duplicate_court_records = int(courts["fir_number"].duplicated().sum())
        verdict = courts.drop_duplicates("fir_number", keep="last").set_index(
            "fir_number")["conviction_status"]
    # Object dtype with None for "no record": a blank or non-string registry value is
    # no record, not a verdict.
    firs["verdict"] = firs["case_id"].map(verdict).map(
        lambda x: x.strip() if isinstance(x, str) and x.strip() else None).astype(object)

    scope_cols = firs.set_index("CaseMasterID")[["district", "crime_type"]]

    def _people(table, cols):
        # Only the listed columns are ever selected — names, caste and religion
        # never enter this module's frames.
        t = _frame(table, ("CaseMasterID",) + cols)
        if t is None:
            return None
        return t.join(scope_cols, on="CaseMasterID", how="inner")

    occ = _frame(src["OccupationMaster"], ("OccupationID", "OccupationName"))
    return {
        "firs": firs,
        "complainants": _people(src["ComplainantDetails"], ("AgeYear", "OccupationID", "GenderID")),
        "victims": _people(src["Victim"], ("AgeYear", "GenderID")),
        "accused": _people(src["Accused"], ("AgeYear", "GenderID", "PersonID")),
        "occupations": (sorted(zip(occ["OccupationID"].astype(int), occ["OccupationName"].astype(str)))
                        if occ is not None else []),
        "districts": sorted(firs["district"].dropna().astype(str).unique().tolist()),
        "crime_types": sorted(firs["crime_type"].dropna().astype(str).unique().tolist()),
        "groups": groups,
        "crime_meta": crime_meta,
        "duplicate_court_records": duplicate_court_records,
    }


# ------------------------------------------------------------------- utilities
def _apportion(counts, total, dp=4):
    """Shares rounded to `dp` places that still sum to exactly 1 (largest
    remainder). Rounding each share on its own lets a breakdown read 100.1%."""
    if total <= 0:
        return [0.0] * len(counts)
    scale = 10 ** dp
    raw = [c * scale / total for c in counts]
    floors = [math.floor(r) for r in raw]
    short = int(round(scale - sum(floors)))
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)[:short]:
        floors[i] += 1
    return [f / scale for f in floors]


def _distribution(labels, counts):
    n = int(sum(counts))
    shares = _apportion(counts, n)
    return [{"name": l, "count": int(c), "share": s} for l, c, s in zip(labels, counts, shares)]


def _age_bands(ages):
    ages = pd.to_numeric(ages, errors="coerce")
    counts = [int(((ages >= lo) & (ages <= hi)).sum()) for _, lo, hi in AGE_BANDS]
    missing = int(len(ages) - sum(counts))
    labels = [b[0] for b in AGE_BANDS]
    if missing:
        labels, counts = labels + [NOT_RECORDED], counts + [missing]
    return [{"band": d["name"], "count": d["count"], "share": d["share"]}
            for d in _distribution(labels, counts)]


def _gender(ids):
    names = pd.to_numeric(ids, errors="coerce").map(GENDER).fillna(NOT_RECORDED)
    vc = names.value_counts()
    labels = list(GENDER_ORDER) + ([NOT_RECORDED] if vc.get(NOT_RECORDED, 0) else [])
    return _distribution(labels, [int(vc.get(l, 0)) for l in labels])


def _evenness(counts):
    """Chi-square goodness of fit against an even split over `counts`.

    `testable` is False when an expected cell falls below 5, where the chi-square
    approximation is not trustworthy and no claim is made either way.
    """
    k, n = len(counts), sum(counts)
    if k < 2 or n == 0:
        return {"testable": False, "chi2": None, "df": None, "critical_5pct": None,
                "near_uniform": None}
    expected = n / k
    chi2 = sum((c - expected) ** 2 / expected for c in counts)
    crit = _chi2_critical_05(k - 1)
    testable = expected >= 5
    return {"testable": testable, "chi2": round(chi2, 2), "df": k - 1,
            "critical_5pct": round(crit, 3),
            "near_uniform": bool(chi2 < crit) if testable else None}


def _pct(x, dp=1):
    return f"{x * 100:.{dp}f}%"


def _canonical(value, options, what):
    """Case-insensitive match of a filter onto the values present in the corpus.

    An unknown value is refused with the valid options rather than answered with
    zeros: an empty chart for a misspelt district reads as "no crime there".
    """
    if value is None or not str(value).strip():
        return None, None
    wanted = str(value).strip().lower()
    for o in options:
        if o.lower() == wanted:
            return o, None
    return None, (jsonify({"error": f"Unknown {what} '{value}'.",
                           "options": options}), 400)


def _unavailable():
    return jsonify({"error": "The FIR corpus is not loaded.", "available": False}), 503


# Results are memoised per filter combination inside the prepared bundle, so they are
# discarded with it when the corpus reloads. The filters are validated against the
# corpus before they become a key, so the key space is bounded by the option lists
# (32 x 11 demographics, 32 x 2 flows); the cap is a backstop, not the expected case.
_RESULTS_CAP = 512


def _memoised(p, key, build):
    results = p.setdefault("results", {})
    hit = results.get(key)
    if hit is None:
        hit = build()
        if len(results) >= _RESULTS_CAP:
            results.clear()
        results[key] = hit
    return hit


# --------------------------------------------------------------- demographics
def build_demographics(district=None, crime_type=None):
    """Aggregate profile of the people on the FIRs in scope. Filters must already
    be canonical (see `_canonical`)."""
    p = _prepared()
    if p is None:
        return None
    firs = p["firs"]
    mask = pd.Series(True, index=firs.index)
    if district:
        mask &= firs["district"] == district
    if crime_type:
        mask &= firs["crime_type"] == crime_type
    n_firs = int(mask.sum())

    def _in_scope(t):
        if t is None:
            return None
        m = pd.Series(True, index=t.index)
        if district:
            m &= t["district"] == district
        if crime_type:
            m &= t["crime_type"] == crime_type
        return t[m]

    base = {
        "filters": {"district": district, "crime_type": crime_type},
        "options": {"districts": p["districts"], "crime_types": p["crime_types"]},
        "firs": n_firs,
        "source": ("ComplainantDetails x OccupationMaster, Victim and Accused, joined to "
                   "the FIRs in scope on CaseMasterID. Aggregate counts only."),
        "age_bands": [b[0] for b in AGE_BANDS],
    }
    if 0 < n_firs < MIN_SCOPE_FIRS:
        return {**base, "withheld": True, "occupation": None, "complainant": None,
                "victim": None, "accused": None, "notes": [],
                "note": (f"Only {n_firs} FIRs match this filter. Breakdowns are withheld "
                         f"below {MIN_SCOPE_FIRS} FIRs so an open endpoint cannot be "
                         "narrowed to a few identifiable people.")}

    notes = []
    note = None

    # ---- complainant occupation
    comp = _in_scope(p["complainants"])
    occupation = None
    complainant = None
    if comp is not None:
        names = dict(p["occupations"])
        occ_ids = pd.to_numeric(comp["OccupationID"], errors="coerce")
        vc = occ_ids.value_counts()
        rows = [(oid, nm, int(vc.get(oid, 0))) for oid, nm in p["occupations"]]
        unmapped = int(len(comp) - sum(r[2] for r in rows))
        if unmapped:
            rows.append((None, NOT_RECORDED, unmapped))
        n = len(comp)
        shares = _apportion([r[2] for r in rows], n)
        cats = [{"id": None if oid is None else int(oid), "name": nm, "count": c, "share": s,
                 # "Others"/"Not recorded" are residual buckets, not an occupation;
                 # the chart greys them instead of giving them a hue.
                 "residual": oid is None or nm.strip().lower() in ("others", "other")}
                for (oid, nm, c), s in zip(rows, shares)]
        cats.sort(key=lambda c: (-c["count"], c["id"] if c["id"] is not None else 1 << 30))
        top = cats[0] if cats and cats[0]["count"] else None
        recorded = [c for c in cats if c["id"] is not None]
        test = _evenness([c["count"] for c in sorted(recorded, key=lambda c: c["id"])])
        occupation = {"n": int(n), "categories": cats, "top": top, "uniformity": test,
                      "categories_defined": len(names)}

        if n and recorded:
            hi = max(recorded, key=lambda c: c["share"])
            lo = min(recorded, key=lambda c: c["share"])
            spread = (hi["share"] - lo["share"]) * 100
            if test["near_uniform"]:
                note = (
                    f"Complainant occupation is consistent with an even split across the "
                    f"{len(recorded)} categories: {hi['name']} ({_pct(hi['share'])}) and "
                    f"{lo['name']} ({_pct(lo['share'])}) differ by {spread:.1f} points, and a "
                    f"chi-square test cannot reject an even split (chi2 = {test['chi2']}, "
                    f"df {test['df']}, 5% critical value {test['critical_5pct']}), so the "
                    "ranking is chance. That is what a simulated corpus that draws "
                    "occupation at random produces — read it as the chart working, not as "
                    "evidence about who files FIRs in Karnataka.")
            elif test["testable"]:
                note = (
                    f"Occupation shares differ by more than chance would explain "
                    f"(chi2 = {test['chi2']}, df {test['df']}, 5% critical value "
                    f"{test['critical_5pct']}); {hi['name']} leads at {_pct(hi['share'])}. "
                    "The corpus is simulated, so this describes how it was generated, "
                    "not Karnataka's complainants.")
            else:
                note = (f"Only {n} complainants in scope — too few to test whether the "
                        "split differs from an even one. Treat the ranking as noise.")

        complainant = {"n": int(n), "age_bands": _age_bands(comp["AgeYear"]),
                       "gender": _gender(comp["GenderID"])}
        mf = [g for g in complainant["gender"] if g["name"] in ("Male", "Female")]
        mf_test = _evenness([g["count"] for g in mf])
        if mf_test["near_uniform"]:
            notes.append(
                f"Complainant gender is close to an even split ({mf[0]['name']} "
                f"{_pct(mf[0]['share'])}, {mf[1]['name']} {_pct(mf[1]['share'])}) — another "
                "field the simulation draws evenly.")

    vict = _in_scope(p["victims"])
    victim = None if vict is None else {
        "n": int(len(vict)), "age_bands": _age_bands(vict["AgeYear"]),
        "gender": _gender(vict["GenderID"])}

    acc = _in_scope(p["accused"])
    accused = None
    if acc is not None:
        accused = {"n": int(len(acc)),
                   "distinct_persons": int(acc["PersonID"].dropna().nunique()),
                   "age_bands": _age_bands(acc["AgeYear"]),
                   "gender": _gender(acc["GenderID"])}
        if len(acc) and accused["distinct_persons"] < len(acc):
            notes.append(
                f"Accused figures count accused-in-FIR records ({len(acc):,}), not people: "
                f"{accused['distinct_persons']:,} distinct persons appear, some on several "
                "FIRs, each time at the age recorded on that FIR.")

    notes.append("Simulated CCTNS-shaped corpus; caste and religion are recorded in the "
                 "source tables and deliberately not broken down here.")
    return {**base, "withheld": False, "occupation": occupation, "complainant": complainant,
            "victim": victim, "accused": accused, "note": note, "notes": notes}


@bp.route(f"{API}/analytics/demographics", methods=["GET"])
def route_demographics():
    p = _prepared()
    if p is None:
        return _unavailable()
    district, err = _canonical(request.args.get("district"), p["districts"], "district")
    if err:
        return err
    crime_type, err = _canonical(request.args.get("crime_type"), p["crime_types"], "crime type")
    if err:
        return err
    return jsonify(_memoised(p, ("demographics", district, crime_type),
                             lambda: build_demographics(district, crime_type)))


# ------------------------------------------------------------------- case flow
def _outcome(status, verdict):
    if status == CHARGE_SHEETED:
        return verdict if isinstance(verdict, str) and verdict else AWAITING
    if status == "Closed":
        return CLOSED_OUTCOME
    if status == "Under Investigation":
        return STILL_UI
    return OTHER_STATUS


def _ordered(values, preferred):
    """`preferred` first in its own order, anything unexpected after, sorted."""
    present = set(values)
    head = [v for v in preferred if v in present]
    return head + sorted(present - set(head))


def build_flow(district=None, group="crime"):
    """crime (or crime-head group) -> FIR status -> outcome, with exact conservation:
    every FIR in scope contributes one unit to exactly one path, so each column sums
    to the FIR count and each status node's inflow equals its outflow."""
    p = _prepared()
    if p is None:
        return None
    firs = p["firs"]
    scope = firs[firs["district"] == district] if district else firs
    n = int(len(scope))
    key_col = "head_group" if group == "head" else "crime_type"

    # A missing status is its own bucket, never defaulted to a real one.
    status = scope["status"].fillna(NOT_RECORDED).astype(str)
    verdict = scope["verdict"]
    outcome = [_outcome(s, v) for s, v in zip(status, verdict)]
    frame = pd.DataFrame({"c": scope[key_col].fillna(UNCLASSIFIED).astype(str).values,
                          "s": status.values, "o": outcome})

    is_cs = status == CHARGE_SHEETED
    has_verdict = verdict.notna()
    court_used = int((is_cs & has_verdict).sum())
    court_ignored = int((~is_cs & has_verdict).sum())

    # Column order. Column 0 follows the CrimeHead master (and SeqID within a head)
    # rather than size, so the order — and therefore which colours sit side by side
    # — does not change when the district filter does.
    meta = p["crime_meta"]
    groups = p["groups"]
    if group == "head":
        c_order = _ordered(frame["c"], groups + [UNCLASSIFIED])
    else:
        c_order = sorted(set(frame["c"]), key=lambda c: (
            meta.get(c, {}).get("group_order", len(groups)), meta.get(c, {}).get("shade", 0), c))
    s_order = _ordered(frame["s"], STATUS_ORDER)
    o_pref = [STILL_UI, *VERDICT_ORDER]
    other_verdicts = sorted(set(frame.loc[frame["s"] == CHARGE_SHEETED, "o"])
                            - set(VERDICT_ORDER) - {AWAITING})
    o_order = _ordered(frame["o"], o_pref + other_verdicts + [AWAITING, CLOSED_OUTCOME, OTHER_STATUS])

    def nid(col, label):
        return f"{'cso'[col]}:{label}"

    def node(col, label, value, extra=None):
        value = int(value)
        d = {"id": nid(col, label), "label": label, "column": col, "value": value,
             "share": round(value / n, 4) if n else 0.0}
        return {**d, **(extra or {})}

    c_counts = frame["c"].value_counts()
    s_counts = frame["s"].value_counts()
    o_counts = frame["o"].value_counts()
    nodes = []
    for c in c_order:
        if group == "head":
            gi = groups.index(c) if c in groups else len(groups)
            extra = {"group": c, "group_order": gi, "shade": 0, "shades": 1}
        else:
            m = meta.get(c, {"group": UNCLASSIFIED, "group_order": len(groups),
                             "shade": 0, "shades": 1})
            extra = dict(m)
        nodes.append(node(0, c, c_counts[c], extra))
    nodes += [node(1, s, s_counts[s]) for s in s_order]
    nodes += [node(2, o, o_counts[o]) for o in o_order]

    rank = {**{nid(0, c): i for i, c in enumerate(c_order)},
            **{nid(1, s): i for i, s in enumerate(s_order)},
            **{nid(2, o): i for i, o in enumerate(o_order)}}

    paths = []
    for (c, s, o), v in frame.groupby(["c", "s", "o"]).size().items():
        paths.append({"source": nid(0, c), "via": nid(1, s), "target": nid(2, o), "value": int(v)})
    paths.sort(key=lambda x: (rank[x["source"]], rank[x["via"]], rank[x["target"]]))

    links = []
    for (a, b), v in frame.groupby(["c", "s"]).size().items():
        links.append({"source": nid(0, a), "target": nid(1, b), "value": int(v), "stage": 1})
    for (a, b), v in frame.groupby(["s", "o"]).size().items():
        links.append({"source": nid(1, a), "target": nid(2, b), "value": int(v), "stage": 2})
    links.sort(key=lambda x: (x["stage"], rank[x["source"]], rank[x["target"]]))

    note = (
        "The middle column is the FIR's status in CaseMaster. The outcome column follows "
        "from it: a charge-sheeted FIR takes its verdict from the court registry (Convicted, "
        "Acquitted or Pending Trial), or 'Awaiting court record' when the registry has "
        "no entry for it; a closed FIR stays 'Closed'; an FIR under investigation is "
        "'Still under investigation'.")
    if court_ignored:
        note += (
            f" The court registry also holds a court record for {court_ignored:,} FIRs in "
            "scope that were never charge-sheeted — a contradiction in the simulated "
            "corpus. The FIR status is taken as authoritative and those records are not "
            "used, so conviction counts here are lower than the lifecycle funnel's, which "
            "counts any recorded verdict.")
    if p["duplicate_court_records"]:
        note += (f" {p['duplicate_court_records']:,} duplicate court records were found; "
                 "the last one per FIR is used.")

    return {
        "filters": {"district": district, "group": group},
        "options": {"districts": p["districts"], "groups": ["crime", "head"]},
        "columns": ["Crime-head group" if group == "head" else "Crime type",
                    "FIR status", "Outcome"],
        "groups": groups,
        "nodes": nodes,
        "links": links,
        "paths": paths,
        "totals": {
            "firs": n,
            "columns": [int(c_counts.sum()), int(s_counts.sum()), int(o_counts.sum())],
            "by_status": {s: int(s_counts[s]) for s in s_order},
            "by_outcome": {o: int(o_counts[o]) for o in o_order},
            "court_records_used": court_used,
            "court_records_ignored": court_ignored,
        },
        "note": note,
        "source": "CaseMaster status, CourtCasesRegistry verdicts, CrimeSubHead -> CrimeHead "
                  "for the head groups. Simulated CCTNS-shaped corpus.",
    }


@bp.route(f"{API}/lifecycle/flow", methods=["GET"])
def route_flow():
    p = _prepared()
    if p is None:
        return _unavailable()
    district, err = _canonical(request.args.get("district"), p["districts"], "district")
    if err:
        return err
    group = (request.args.get("group") or "crime").strip().lower()
    if group not in ("crime", "head"):
        return jsonify({"error": f"Unknown group '{group}'.", "options": ["crime", "head"]}), 400
    return jsonify(_memoised(p, ("flow", district, group), lambda: build_flow(district, group)))


__all__ = ["bp", "configure", "build_demographics", "build_flow", "MIN_SCOPE_FIRS"]
