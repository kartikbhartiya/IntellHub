"""Contestability: a person's route to challenge what the system says about them.

Why this exists
---------------
The governance statement claims these models "order a queue for a person to work" and
that no output authorises an action against an individual. Both are true, and both
are weaker than they sound while a person has no way to find out they are on a
watchlist, no way to see why, and no way to object. Automated assessment without a
route to challenge it is the failure mode every framework for this — the DPDP Act's
grievance redressal duty included — exists to prevent.

Three things are needed and all three are here:

1. **Disclosure.** `subject_report` assembles everything the system holds and asserts
   about one person: every score with its date, the model's own stated limits, who
   accessed the record and why. A person cannot contest an assessment they cannot see.
2. **A record of the challenge.** `raise_contest` writes it down, against a specific
   subject and model, with the grounds stated.
3. **Consequence.** `is_contested` is checked at serving time, so a contested score
   travels marked wherever it is displayed. A challenge that changed nothing about
   what the officer sees would be a complaints box, not a control.

What this deliberately does not do
----------------------------------
It does not adjudicate. Nothing here decides whether a challenge is well-founded, and
nothing automatically suppresses a score — that is a decision for the deploying
authority, not for this module to make silently. What it guarantees is that the
challenge is recorded, disclosed alongside the assessment, and visible to whoever
acts on it.
"""
import json
import os
import sys
import threading
from datetime import datetime, timezone

_DIR = os.path.dirname(os.path.abspath(__file__))
CONTEST_LOG = os.environ.get("CONTEST_LOG_PATH") or os.path.join(_DIR, "contests.jsonl")

_LOCK = threading.Lock()

# Open challenges, keyed by subject. Held in memory so the serving path can mark a
# score without a file read per request; the file is the durable record.
_open = {}
_loaded = False

STATUSES = ("open", "upheld", "rejected", "withdrawn")


class UnknownStatus(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load():
    """Replay the log into the in-memory index. Cheap; the file is small by nature."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    _open.clear()
    try:
        with open(CONTEST_LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                subject = str(entry.get("subject_id"))
                if entry.get("status") == "open":
                    _open.setdefault(subject, []).append(entry)
                else:
                    # A resolution supersedes the open record it names.
                    rows = _open.get(subject) or []
                    _open[subject] = [r for r in rows
                                      if r.get("id") != entry.get("contest_id")]
                    if not _open[subject]:
                        _open.pop(subject, None)
    except OSError:
        pass


def _append(entry):
    line = json.dumps(entry, separators=(",", ":"), default=str)
    try:
        print("CONTEST " + line, file=sys.stdout, flush=True)
    except Exception:
        pass
    try:
        with open(CONTEST_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + chr(10))
    except Exception as e:
        print("CONTEST-SINK-FAILURE " + str(e), file=sys.stderr, flush=True)


def _next_id(subject):
    """A challenge id that cannot collide with another for the same person.

    Second precision was not enough: two challenges filed in the same second shared
    an id, and `resolve_contest` clears by id, so closing one closed the other. The
    register would then report a challenge resolved that nobody had read.

    Microseconds make a natural collision vanishingly unlikely; the suffix loop
    makes it impossible, including against ids already resolved and therefore absent
    from the in-memory index. Caller holds _LOCK.
    """
    stamp = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    base = f"CON-{subject}-{stamp}"
    taken = {row.get("id") for row in history(subject)}
    taken |= {row.get("id") for row in (_open.get(subject) or [])}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def raise_contest(subject_id, model=None, grounds=None, raised_by=None,
                  on_behalf_of_subject=True):
    """Record a challenge to an assessment.

    `grounds` is required. A contest with no stated basis cannot be reviewed, and an
    unreviewable challenge helps the person who filed it least of all.
    """
    if not grounds or not str(grounds).strip():
        raise ValueError("grounds are required — an unreviewable challenge helps "
                         "the person who raised it least of all")

    with _LOCK:
        _load()
        subject = str(subject_id)
        entry = {
            "id": _next_id(subject),
            "ts": _now(),
            "subject_id": subject,
            "model": model,
            "grounds": str(grounds).strip(),
            "raised_by": raised_by,
            "on_behalf_of_subject": bool(on_behalf_of_subject),
            "status": "open",
        }
        _open.setdefault(subject, []).append(entry)
        _append(entry)
        return entry


def resolve_contest(contest_id, subject_id, status, note=None, resolved_by=None):
    """Close a challenge. The original entry is never rewritten — the resolution is
    appended, so the history of a disputed assessment stays readable."""
    if status not in STATUSES or status == "open":
        raise UnknownStatus(
            f"{status!r} is not a resolution; expected one of "
            f"{sorted(set(STATUSES) - {'open'})}")

    with _LOCK:
        _load()
        subject = str(subject_id)
        entry = {
            "id": f"{contest_id}-resolution",
            "contest_id": contest_id,
            "ts": _now(),
            "subject_id": subject,
            "status": status,
            "note": note,
            "resolved_by": resolved_by,
        }
        rows = _open.get(subject) or []
        _open[subject] = [r for r in rows if r.get("id") != contest_id]
        if not _open[subject]:
            _open.pop(subject, None)
        _append(entry)
        return entry


def is_contested(subject_id):
    """Checked on the serving path. A challenge that changed nothing about what the
    officer sees would be a complaints box, not a control."""
    with _LOCK:
        _load()
        return bool(_open.get(str(subject_id)))


def open_contests(subject_id=None):
    with _LOCK:
        _load()
        if subject_id is not None:
            return list(_open.get(str(subject_id)) or [])
        return [row for rows in _open.values() for row in rows]


def history(subject_id):
    """Every entry ever written for one subject, open or resolved."""
    subject = str(subject_id)
    rows = []
    try:
        with open(CONTEST_LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if str(entry.get("subject_id")) == subject:
                    rows.append(entry)
    except OSError:
        pass
    return rows


def subject_report(subject_id, registry=None):
    """Everything the system holds and asserts about one person.

    This is the disclosure a challenge has to be built on: the scores, when they were
    made, the model's own stated limits, who looked at the record, and any challenge
    already raised. Handing someone a risk band with no basis and no history is not
    disclosure, and a contest route without it would be decorative.
    """
    from ml import monitoring, outcomes

    subject = str(subject_id)

    # From the log, not the in-memory deque: the deque is capped and empty after a
    # restart, so this used to tell a person no assessment had been made while their
    # score sat on disk.
    scores = monitoring.predictions_for(subject)

    recorded = [o for o in outcomes._read(outcomes.OUTCOME_LOG)
                if str(o.get("subject_id")) == subject]

    accesses = []
    try:
        import audit
        accesses = [
            {"ts": e.get("ts"), "actor": e.get("actor"), "role": e.get("role"),
             "action": e.get("action"), "reason": e.get("reason"),
             "reason_given": e.get("reason_given")}
            for e in audit.recent(limit=2000, subject_id=subject)
        ]
    except Exception:
        pass

    limits = {}
    registry = registry if registry is not None else _read_registry()
    for name in {s.get("model") for s in scores if s.get("model")}:
        entry = (registry.get("models") or {}).get(name) or {}
        governance = entry.get("governance") or {}
        limits[name] = {
            "intended_use": governance.get("intended_use"),
            "out_of_scope_use": governance.get("out_of_scope_use"),
            "headline": entry.get("headline"),
            "known_limits": entry.get("notes"),
        }

    return {
        "subject_id": subject,
        "assessments": sorted(scores, key=lambda s: str(s.get("ts") or "")),
        "recorded_outcomes": recorded,
        "model_limits": limits,
        "who_accessed_this_record": accesses,
        "contests": history(subject),
        "currently_contested": is_contested(subject),
        "note": "Every score here is decision support. None of it establishes a fact "
                "about this person, and none of it may be presented as evidence. A "
                "score can be challenged through the contest route; a challenge is "
                "recorded and shown alongside the assessment, and does not by itself "
                "remove it.",
    }


def _read_registry():
    path = os.path.join(_DIR, "artifacts", "model_registry.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def reset_for_tests():
    """Drop the in-memory index so a test can rebind CONTEST_LOG."""
    global _loaded
    with _LOCK:
        _loaded = False
        _open.clear()
