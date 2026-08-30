"""Deterministic answers for the copilot, computed from the loaded corpus.

Why this exists
---------------
Every factual question put to the agent fell through to "I could not reach my
reasoning core" — "how many cases are there", "which district has the most crime",
"what is the conviction rate". All of it sits in `data_cache`; none of it needed a
language model. The copilot was advertising itself on every page and then declining
to answer the easiest questions in the product.

Two rules this module holds to
------------------------------
**Every number is computed, never asserted.** Each answer carries a `grounding` block
naming the figures it used and where they came from, so a reply can be checked rather
than believed. A handler that cannot compute its answer returns None and the caller
falls through — a wrong number stated confidently is worse than no answer.

**It refuses what the platform refuses.** GOVERNANCE.md says this system does not
infer guilt and that no output authorises an action against an individual. The
copilot is the surface most likely to be asked to do both, in exactly those words, and
it used to respond to "is SUS-1001 guilty" by quietly opening the dossier. A refusal
that explains itself is part of the control, not a limitation of it.

List-of-people questions route to the gated registry views rather than printing names
into a chat transcript. The route already requires `investigator`, so this is not
about access — a transcript is simply the wrong place for a watchlist.
"""
import re

# --------------------------------------------------------------------- refusals
# Ordered: the first match wins, so the most specific claim is caught first.
_REFUSALS = [
    (re.compile(r"\b(is|was|are)\b.{0,30}\b(guilty|innocent|the culprit|responsible)\b", re.I),
     "I cannot answer that, and the platform is built so that nothing here can. "
     "These models order a queue for a person to work; none of them establishes a "
     "fact about anyone. Guilt is determined by a court on evidence, and no score in "
     "this system is evidence or may be presented as such. What I can show you is the "
     "record: the cases naming this person, who accessed the file, and what each "
     "model does and does not claim."),

    (re.compile(r"\b(arrest|detain|charge|convict|pick\s*up|raid|search)\b\s+\S*\s*(sus[-\s]?\d+|him|her|them|this (person|suspect))", re.I),
     "I cannot authorise or recommend an action against a named individual. No output "
     "in this platform triggers an action automatically, and every threshold in it "
     "orders a queue for a person to review — that limit is deliberate and is stated "
     "in GOVERNANCE.md. I can open the dossier so an officer can decide."),

    (re.compile(r"\b(predict|will|going to)\b.{0,24}\b(commit|reoffend|offend)\b.{0,24}\b(next|again|tomorrow|will)\b", re.I),
     "I will not phrase a risk score as a prediction that a named person will commit "
     "a crime. The recidivism model estimates, from dated history only, how a cohort "
     "with a similar record has behaved — it is a way of ordering attention, not a "
     "forecast about an individual, and it is calibrated and published with its "
     "failures for exactly that reason."),
]


def refusal_for(text):
    """The refusal this question earns, or None."""
    for pattern, reply in _REFUSALS:
        if pattern.search(text or ""):
            return reply
    return None


# ---------------------------------------------------------------------- helpers
def _frame(cache, key):
    df = cache.get(key)
    return df if df is not None and hasattr(df, "empty") and not df.empty else None


def _district_in(text, cache):
    """A district named in the question, matched against the ones that exist."""
    firs = _frame(cache, "firs")
    if firs is None or "district" not in firs:
        return None
    low = (text or "").lower()
    best = None
    for name in firs["district"].dropna().unique():
        n = str(name)
        if n.lower() in low and (best is None or len(n) > len(best)):
            best = n           # longest match, so "Bengaluru Urban" beats "Bengaluru"
    return best


def _crime_in(text, cache):
    firs = _frame(cache, "firs")
    if firs is None or "crime_type" not in firs:
        return None
    low = (text or "").lower()
    best = None
    for name in firs["crime_type"].dropna().unique():
        n = str(name)
        # Match on the distinctive first word too: "murders" should find "Murder".
        head = n.split("(")[0].strip().lower()
        if head and (head in low or head.rstrip("s") in low or f"{head}s" in low):
            if best is None or len(n) > len(best):
                best = n
    return best


_REGISTRY_CACHE = {}


def _registry():
    """The published model report, read from the artifact it is written to.

    It is not in `data_cache` — the app caches per-model metric slices under separate
    keys — so the handler that reached for `cache["model_registry"]` always found
    nothing. Read once and kept, because the file only changes on a retrain.
    """
    import json
    import os

    if "data" in _REGISTRY_CACHE:
        return _REGISTRY_CACHE["data"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "artifacts", "model_registry.json")
    try:
        with open(path, encoding="utf-8") as fh:
            _REGISTRY_CACHE["data"] = json.load(fh)
    except (OSError, ValueError):
        _REGISTRY_CACHE["data"] = {}
    return _REGISTRY_CACHE["data"]


def _answer(reply, grounding, action="answer", params=None, suggestions=None):
    return {"action": action, "params": params or {}, "reply": reply,
            "grounding": grounding, "suggestions": suggestions or []}


# --------------------------------------------------------------------- handlers
def _corpus_size(text, cache):
    if not re.search(r"how many (cases|firs|records|suspects|offenders|people)", text, re.I):
        return None
    firs = _frame(cache, "firs")
    suspects = _frame(cache, "suspects")
    if firs is None:
        return None
    n_cases = len(firs)
    n_susp = len(suspects) if suspects is not None else None
    # The corpus stores timestamps as strings, so they have to be coerced before they
    # can be formatted as dates — the naive version raised ValueError and the handler
    # was dropped silently by the caller's except.
    span = ""
    col = "ts" if "ts" in firs else ("timestamp" if "timestamp" in firs else None)
    if col:
        import pandas as pd
        t = pd.to_datetime(firs[col], errors="coerce").dropna()
        if len(t):
            span = f", spanning {t.min():%Y-%m-%d} to {t.max():%Y-%m-%d}"
    reply = (f"The corpus holds {n_cases:,} FIRs"
             + (f" and {n_susp:,} tracked persons" if n_susp else "") + span
             + ". It is CCTNS-schema-shaped simulated data, not real KSP records — "
               "every figure I give you is a measurement of that corpus.")
    return _answer(reply, {"cases": n_cases, "tracked_persons": n_susp,
                           "source": "data_cache['firs'], data_cache['suspects']"})


def _district_ranking(text, cache):
    if not re.search(r"(which|what|top).{0,24}(district|area|region)", text, re.I):
        return None
    if not re.search(r"(most|highest|worst|top|rank)", text, re.I):
        return None
    firs = _frame(cache, "firs")
    if firs is None or "district" not in firs:
        return None
    counts = firs["district"].value_counts().head(5)
    lines = ", ".join(f"{d} ({n:,})" for d, n in counts.items())
    reply = (f"By recorded FIR volume the leading districts are: {lines}. "
             "That is a count of what was *recorded*, which reflects reporting and "
             "enforcement patterns as much as underlying crime — the same caveat the "
             "governance statement makes about feedback loops.")
    return _answer(reply, {"top_districts": {str(d): int(n) for d, n in counts.items()},
                           "source": "value_counts over data_cache['firs'].district"},
                   suggestions=[{"label": "Show the heatmap", "query": "show heatmap"}])


def _filtered_count(text, cache):
    """"how many murders in Belagavi" — a count with both filters named."""
    if not re.search(r"how many", text, re.I):
        return None
    firs = _frame(cache, "firs")
    if firs is None:
        return None
    district = _district_in(text, cache)
    crime = _crime_in(text, cache)
    if not district and not crime:
        return None
    sel = firs
    parts = []
    if crime:
        sel = sel[sel["crime_type"] == crime]
        parts.append(f"{crime}")
    if district:
        sel = sel[sel["district"] == district]
        parts.append(f"in {district}")
    n = len(sel)
    share = (n / len(firs) * 100) if len(firs) else 0
    reply = (f"{n:,} recorded {' '.join(parts)} — {share:.1f}% of the {len(firs):,} "
             f"FIRs in the corpus.")
    return _answer(reply, {"matched": n, "total": len(firs),
                           "crime_type": crime, "district": district,
                           "source": "filtered count over data_cache['firs']"},
                   suggestions=[{"label": "Open these in the directory",
                                 "query": f"show {crime or ''} cases {('in ' + district) if district else ''}".strip()}])


def _conviction_rate(text, cache):
    if not re.search(r"conviction rate|what.{0,12}conviction|how many convict", text, re.I):
        return None
    ext = (cache.get("stats_extended") or {}) if isinstance(cache.get("stats_extended"), dict) else {}
    courts = _frame(cache, "CourtCasesRegistry")
    if courts is not None and "conviction_status" in courts:
        vals = courts["conviction_status"].astype(str).str.lower()
        convicted = int((vals == "convicted").sum())
        decided = int(vals.isin(["convicted", "acquitted"]).sum())
        if decided:
            rate = convicted / decided * 100
            reply = (f"{rate:.1f}% of decided trials ended in conviction "
                     f"({convicted:,} of {decided:,}). Cases still pending trial are "
                     "excluded — including them would understate the rate by counting "
                     "unfinished cases as failures.")
            return _answer(reply, {"convicted": convicted, "decided": decided,
                                   "rate_pct": round(rate, 2),
                                   "source": "CourtCasesRegistry.CaseStatus"})
    if ext.get("conviction_rate") is not None:
        return _answer(f"The recorded conviction rate is {ext['conviction_rate']}%.",
                       {"conviction_rate": ext["conviction_rate"], "source": "stats_extended"})
    return None


def _rising_trend(text, cache):
    if not re.search(r"(rising|increas|spike|trend|growing|surge)", text, re.I):
        return None
    alerts = cache.get("trend_alerts") or []
    if not alerts:
        return None
    top = alerts[:3]
    def _fmt(a):
        return (f"{a.get('crime_type', 'unknown')} in {a.get('district', 'unknown')} "
                f"({a.get('ratio', a.get('multiplier', '?'))}x the historical baseline)")
    reply = ("Emerging trend alerts, last 30 days against the historical baseline: "
             + "; ".join(_fmt(a) for a in top)
             + ". These are volume deviations, not causal claims — a spike can mean a "
               "change in reporting as easily as a change in offending.")
    return _answer(reply, {"alerts": top, "source": "data_cache['trend_alerts']"},
                   suggestions=[{"label": "Open analytics", "query": "show analytics"}])


def _model_quality(text, cache):
    if not re.search(r"(accurate|accuracy|reliable|how good|trust|performance).{0,30}model"
                     r"|model.{0,30}(accurate|accuracy|reliable|how good|trust)", text, re.I):
        return None
    models = (_registry() or {}).get("models") or {}
    if not models:
        return None
    low = text.lower()
    picked = None
    for name in models:
        if name.replace("_", " ") in low or name in low:
            picked = name
            break
        head = name.split("_")[0]
        if head and head in low:
            picked = name
            break
    if not picked:
        return None
    m = models[picked]
    mt = m.get("metrics") or {}
    bl = m.get("baselines") or []
    headline = m.get("headline") or ""
    trivial = next((b for b in bl if b.get("roc_auc") == 0.5 or "base rate" in str(b.get("strategy", "")).lower()), None)
    bits = [f"{picked}: {headline}" if headline else picked]
    if mt.get("roc_auc") is not None:
        bits.append(f"ROC-AUC {mt['roc_auc']}" + (" against 0.5 for a coin flip" if trivial else ""))
    if mt.get("brier") is not None:
        bits.append(f"Brier {mt['brier']}")
    notes = [n for n in (m.get("notes") or []) if "not beat" in n.lower() or "indistinguishable" in n.lower()]
    reply = ". ".join(bits) + "."
    if notes:
        reply += " " + notes[0]
    reply += (" Every model is validated on a window later in time than everything it "
              "learned from, and the registry publishes the baselines it fails to beat.")
    return _answer(reply, {"model": picked, "metrics": {k: mt.get(k) for k in ("roc_auc", "brier", "accuracy") if k in mt},
                           "source": "artifacts/model_registry.json"},
                   suggestions=[{"label": "Open the model registry", "query": "show models"}])


def _person_summary(text, cache):
    m = re.search(r"who is\s+(sus[-\s]?\d+)", text, re.I)
    if not m:
        return None
    sid = m.group(1).upper().replace(" ", "-")
    if "-" not in sid:
        sid = sid.replace("SUS", "SUS-")
    suspects = _frame(cache, "suspects")
    if suspects is None or "suspect_id" not in suspects:
        return None
    row = suspects[suspects["suspect_id"].astype(str) == sid]
    if row.empty:
        return _answer(f"No person with identifier {sid} exists in the registry.",
                       {"suspect_id": sid, "found": False, "source": "data_cache['suspects']"})
    r = row.iloc[0]
    parts = [f"{r.get('name', sid)} ({sid})"]
    for label, col in (("age", "age"), ("district", "district"),
                       ("risk band", "risk_level"), ("affiliation", "gang_affiliation")):
        if col in r and str(r.get(col)) not in ("nan", "None", ""):
            parts.append(f"{label} {r.get(col)}")
    reply = (", ".join(parts)
             + ". This is the registry record only. Any risk band shown is decision "
               "support for allocating attention — it is not evidence and establishes "
               "nothing about this person.")
    return _answer(reply, {"suspect_id": sid, "found": True,
                           "source": "data_cache['suspects']"},
                   action="open_suspect", params={"suspect_id": sid})


def _pendency(text, cache):
    if not re.search(r"(pendenc|past \d+ ?day|overdue|still open|awaiting chargesheet|sitting)", text, re.I):
        return None
    p = cache.get("pendency") or {}
    summary = p.get("summary") or p
    open_cases = summary.get("open_cases") or summary.get("total_open")
    if open_cases is None:
        return None
    breached = summary.get("past_90d") or summary.get("breached_90d")
    reply = f"{int(open_cases):,} FIRs have no chargesheet recorded at the extract date"
    if breached is not None:
        reply += f", of which {int(breached):,} are already past the 90-day mark"
    reply += (". The pendency model treats a case still open as censored rather than "
              "failed, which is what lets a recently registered FIR be scored at all.")
    return _answer(reply, {"open_cases": open_cases, "past_90d": breached,
                           "source": "data_cache['pendency']"},
                   suggestions=[{"label": "Open pendency", "query": "show pendency"}])


def _watchlist(text, cache):
    """Questions asking for a ranked list of people.

    Answered with the shape of the list and a route to the gated view, never with the
    names themselves. `/agent` already requires `investigator`, so this is not about
    access — a chat transcript is simply the wrong place for a watchlist to live, and
    it is the one artefact of this system most likely to be screenshotted.
    """
    if not re.search(r"(top|most|highest|worst|list).{0,26}"
                     r"(rowdy|sheeter|offender|suspect|criminal|risk|reoffend)", text, re.I):
        return None
    top = cache.get("top_suspects") or []
    n = len(top)
    if not n:
        return None
    reply = (f"{n} people are currently ranked on the recidivism watchlist. I will not "
             "list names here — a watchlist belongs in the gated registry, not in a "
             "chat transcript. Opening it now. Every entry there is a risk band for "
             "allocating attention, not a finding about the person.")
    return _answer(reply, {"watchlist_size": n, "names_withheld": True,
                           "source": "data_cache['top_suspects']"},
                   action="show_registry", params={"kind": "criminals"})


def _audit_summary(text, cache):
    """What the access log records — the control, not its contents."""
    if not re.search(r"audit|who (accessed|looked|viewed)|access log", text, re.I):
        return None
    try:
        import audit
        summary = audit.summary()
    except Exception:
        return None
    chain = summary.get("chain") or {}
    reply = (f"The access log holds {summary.get('entries_in_memory', 0)} recent entries, "
             f"{summary.get('accesses_without_stated_reason', 0)} of them without a "
             "stated reason. Every person-level access records who looked, at whose "
             "record, and why; reading the log is itself audited. Entries are "
             f"hash-chained{' with a keyed digest' if chain.get('keyed') else ''}, so any "
             "edit, reorder or deletion is detectable through /audit-log/verify. "
             "Supervisors can open it; I will not reproduce its contents here.")
    return _answer(reply, {"entries_in_memory": summary.get("entries_in_memory"),
                           "chain_keyed": chain.get("keyed"),
                           "source": "audit.summary()"},
                   suggestions=[{"label": "Verify the audit chain",
                                 "query": "verify the audit chain"}])


_HANDLERS = (_corpus_size, _district_ranking, _filtered_count, _conviction_rate,
             _rising_trend, _model_quality, _person_summary, _pendency,
             _watchlist, _audit_summary)


def answer(text, cache):
    """The first handler that can answer from data, or None to fall through."""
    if not text or cache is None:
        return None
    for handler in _HANDLERS:
        try:
            result = handler(text, cache)
        except Exception:
            # A handler that breaks must not take the copilot down with it; falling
            # through to the LLM or to the guidance reply is always survivable.
            continue
        if result:
            return result
    return None
