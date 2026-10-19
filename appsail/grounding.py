"""Check that the identifiers a model names actually exist.

The problem
-----------
The copilot's freeform `reply` was passed to the client verbatim. Nothing verified
it, and the system prompt actively pushed the other way — *"ASSERTIVE REASONING:
You may reason, infer, rank, and recommend. Act confidently"* — over a 1024-token
channel with no output validation. The reply was then **spoken aloud** by the
browser, which makes an invented statement sound more authoritative, not less.

In a policing context the specific danger is narrow and serious: a fluent sentence
naming a case or a person that does not exist, or attributing an offence to the
wrong identifier. That is not a hallucination an operator can spot, because it
looks exactly like the true ones.

What this does, and does not do
-------------------------------
It extracts every identifier-shaped token from a reply — case numbers, offender
ids, officer KGIDs — and checks each against the loaded corpus. Unknown ones are
returned so the caller can mark the answer as unverified.

This grounds *references*, not *claims*. "SUS-1042 has 9 prior cases" is verified
only insofar as SUS-1042 exists; the count is not checked. Verifying arbitrary
natural-language claims against a relational corpus is a much larger problem, and
pretending this solves it would be its own form of overclaiming. What it does
reliably catch is the invented identifier, which is the failure that turns a
plausible answer into a wrong one about a real person.
"""
import re

# Identifier shapes used across the corpus.
PATTERNS = {
    "case": re.compile(r"\b(?:FIR-)?(\d{15,20})\b"),
    "offender": re.compile(r"\bSUS-(\d{3,6})\b", re.IGNORECASE),
    "officer": re.compile(r"\b(KGID\d{4,8})\b", re.IGNORECASE),
}

CAUTION = ("[unverified: this answer refers to identifiers not present in the "
           "corpus — treat it as unreliable]")


def _known_sets(data_cache):
    """Uppercased identifier sets, built once and cached on the corpus.

    These are fully determined by the loaded corpus, but were rebuilt on every /chat
    and /agent request — three full-column scans over ~18,000 rows — and the
    uppercasing was redone per regex match, so a reply naming ten case numbers
    rebuilt an 18,000-element set ten times.
    """
    cached = data_cache.get("_grounding_sets")
    if cached is not None:
        return cached

    firs = data_cache.get("firs")
    suspects = data_cache.get("suspects")
    officers = data_cache.get("officers")
    sets = {
        "case": (set(firs["case_id"].astype(str)) if firs is not None and not firs.empty
                 else set()),
        "offender": (set(suspects["suspect_id"].astype(str).str.upper())
                     if suspects is not None and not suspects.empty else set()),
        "officer": (set(officers["officer_id"].astype(str).str.upper())
                    if officers is not None and not officers.empty else set()),
    }
    try:
        data_cache["_grounding_sets"] = sets
    except Exception:
        pass
    return sets


def check(text, data_cache):
    """Verify identifiers in `text`. Returns a report; never raises."""
    report = {"checked": 0, "verified": 0, "unknown": [], "grounded": True}
    if not text:
        return report
    try:
        known = _known_sets(data_cache)
    except Exception:
        return report          # cannot verify is not the same as unverified

    text = str(text)
    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            raw = match.group(1)
            candidate = {
                "case": raw,
                "offender": f"SUS-{raw}",
                "officer": raw.upper(),
            }[kind]
            if not known[kind]:
                continue       # that registry is not loaded; do not accuse the model
            report["checked"] += 1
            # `known` is already uppercased for the id kinds; case numbers are digits.
            if candidate.upper() in known[kind] or candidate in known[kind]:
                report["verified"] += 1
            else:
                report["unknown"].append({"kind": kind, "value": match.group(0)})

    report["grounded"] = not report["unknown"]
    return report


def annotate(text, report):
    """Prefix an unverified answer so the caution travels with it.

    Deliberately part of the text rather than a sibling field: the reply is spoken
    aloud and copied around, and a flag in a JSON envelope does not survive either.
    """
    if not text or report.get("grounded", True):
        return text
    return f"{CAUTION}\n\n{text}"


# Actions the client's switch statement can act on. Anything else is inert there,
# so an unrecognised value silently does nothing — the router was contained only by
# that accident. Validating here makes it a decision instead.
KNOWN_ACTIONS = {
    "open_case", "open_suspect", "show_heatmap", "show_network", "show_dispatch",
    "show_anomalies", "show_analytics", "recent_cases", "show_registry",
    "show_suspects", "open_copilot", "guide", "answer", "investigate",
    "show_lifecycle", "show_pendency", "show_models", "show_map", "show_surveillance",
}


def safe_action(action):
    """Unknown actions degrade to `answer` rather than being sent on unchecked."""
    return action if action in KNOWN_ACTIONS else "answer"
