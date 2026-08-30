"""Retention schedules and the erasure path.

Why this exists
---------------
Three runtime logs accumulate personal data indefinitely: who accessed whose record
(`audit.jsonl`), what score each person was given (`predictions.jsonl`), and what
happened to them (`outcomes.jsonl`). Under the DPDP Act 2023 personal data may be
kept only as long as the purpose requires, and a data principal may ask for erasure.
Neither was implemented, and "we never delete anything" is not a defensible answer
for a system holding police records.

The hard part: erasure conflicts with the audit chain
-----------------------------------------------------
The audit log is hash-chained precisely so that entries cannot be removed. Erasing a
person from it would break every digest after their entry — which is the chain
working correctly, not a bug to route around. Rewriting the chain to hide the removal
would defeat the entire control.

So the two are separated, and the reasoning is stated rather than hidden:

* **Prediction and outcome logs** hold *assessments of* a person. These are erased on
  request. They carry no chain, so rewriting them is sound.
* **The audit log** holds a record of *who looked at* a person — evidence of official
  conduct, not an assessment of the subject. It is not erased on request. It is
  bounded by a retention schedule instead, and enforced at whole-file granularity so
  the chain inside each file stays verifiable.

That distinction is the standard resolution, and it is the honest one: an access log
that a subject could delete would let anyone who improperly viewed a record erase the
proof by filing a request in the subject's name.

Every erasure is itself audited, naming what was erased and what deliberately was not.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_DIR = os.path.dirname(os.path.abspath(__file__))

# Days each sink is kept, and why. The justifications are the point: a schedule
# without one is a number someone made up, and cannot be defended to a regulator.
SCHEDULE = {
    "audit": {
        "days": 365 * 3,
        "path": os.path.join(_DIR, "audit.jsonl"),
        "holds": "who accessed which person's record, and the reason they stated",
        "why": "Evidence of official conduct. Three years covers the period in which "
               "an improper access could plausibly be identified and investigated. "
               "Not erasable on subject request — see module docstring.",
        "erasable_on_request": False,
    },
    "predictions": {
        "days": 365 * 2,
        "path": os.path.join(_DIR, "predictions.jsonl"),
        "holds": "the score each person was given, and when",
        "why": "Kept long enough to be joined to an outcome once one is known; the "
               "longest observation window in use is one year, so two years leaves "
               "room for a late result without keeping scores indefinitely.",
        "erasable_on_request": True,
    },
    "outcomes": {
        "days": 365 * 2,
        "path": os.path.join(_DIR, "outcomes.jsonl"),
        "holds": "what actually happened to a person who was scored",
        "why": "Paired with the prediction log to measure realised performance. Kept "
               "for the same period so a pair never survives half-erased.",
        "erasable_on_request": True,
    },
}


def _parse(ts):
    """Parse a log timestamp, always returning an aware datetime.

    Writers do not all produce the same shape: `datetime.now(timezone.utc)` is
    offset-aware, while a timestamp carried over from source data is usually naive.
    Comparing the two raises TypeError, and here that took out the whole retention
    endpoint with a 500 — a data-protection control cannot be one unusual log line
    away from being unable to report its own schedule. A naive stamp is read as UTC,
    which is what every writer in this codebase means by one.
    """
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rows(path):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line
    except OSError:
        return


def status():
    """What is held, for how long, and how much of it is already overdue."""
    now = datetime.now(timezone.utc)
    report = {}
    for name, policy in SCHEDULE.items():
        cutoff = now - timedelta(days=policy["days"])
        total = overdue = 0
        oldest = None
        for line in _rows(policy["path"]):
            total += 1
            # One malformed line must never stop the report. Not knowing a single
            # entry's age is survivable; not being able to answer "what do we hold
            # and for how long" is not.
            try:
                ts = _parse(json.loads(line).get("ts"))
            except (ValueError, TypeError, AttributeError):
                continue
            if ts is None:
                continue
            if oldest is None or ts < oldest:
                oldest = ts
            if ts < cutoff:
                overdue += 1
        report[name] = {
            "retention_days": policy["days"],
            "holds": policy["holds"],
            "justification": policy["why"],
            "erasable_on_subject_request": policy["erasable_on_request"],
            "entries": total,
            "past_retention": overdue,
            "oldest_entry": oldest.isoformat(timespec="seconds") if oldest else None,
            "enforcement": ("whole-file, to keep the hash chain verifiable"
                            if name == "audit" else "per-entry"),
        }
    report["_note"] = (
        "Nothing here runs on a timer. AppSail has no scheduler, so enforcement is "
        "a call to `purge()` — from an operator, or from a job outside this "
        "application. A schedule that is documented but never executed is not a "
        "retention policy, and this endpoint exists so the gap is visible.")
    return report


def purge(sink=None, dry_run=True, now=None):
    """Delete entries past their retention date.

    Defaults to `dry_run=True`. Deleting personal data is correct and irreversible at
    the same time; a caller that meant to inspect and instead destroyed three years
    of access records has no way back.

    The audit sink is enforced at whole-file granularity: rewriting it line by line
    would break the hash chain, and a chain that gets rebuilt whenever it is
    inconvenient proves nothing. Its rolled and pre-chain files are removed once
    every entry in them is past retention; the live file is left alone.
    """
    now = now or datetime.now(timezone.utc)
    names = [sink] if sink else list(SCHEDULE)
    result = {"dry_run": dry_run, "sinks": {}}

    for name in names:
        policy = SCHEDULE.get(name)
        if not policy:
            raise ValueError(f"unknown sink {name!r}; known: {sorted(SCHEDULE)}")
        cutoff = now - timedelta(days=policy["days"])
        path = policy["path"]

        if name == "audit":
            removed_files = []
            for suffix in (".1", ".pre-chain"):
                rolled = path + suffix
                if not os.path.exists(rolled):
                    continue
                newest = None
                for line in _rows(rolled):
                    try:
                        ts = _parse(json.loads(line).get("ts"))
                    except ValueError:
                        continue
                    if ts and (newest is None or ts > newest):
                        newest = ts
                # Only when the *newest* entry in the file is past retention, so a
                # single recent line keeps the whole file.
                if newest and newest < cutoff:
                    if not dry_run:
                        try:
                            os.remove(rolled)
                        except OSError as e:
                            print("RETENTION-FAILURE " + str(e), file=sys.stderr)
                            continue
                    removed_files.append(os.path.basename(rolled))
            result["sinks"][name] = {
                "granularity": "whole-file",
                "files_removed": removed_files,
                "live_file_untouched": True,
                "note": "Per-entry deletion would break the hash chain.",
            }
            continue

        kept, dropped = [], 0
        for line in _rows(path):
            try:
                ts = _parse(json.loads(line).get("ts"))
            except ValueError:
                kept.append(line)          # unparseable: keep, do not silently destroy
                continue
            if ts and ts < cutoff:
                dropped += 1
            else:
                kept.append(line)

        if dropped and not dry_run:
            _rewrite(path, kept)
        result["sinks"][name] = {"granularity": "per-entry", "removed": dropped,
                                 "remaining": len(kept)}
    return result


def _rewrite(path, lines):
    """Replace a log atomically, so an interrupted purge cannot truncate it."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")
    os.replace(tmp, path)


def erase_subject(subject_id, dry_run=True, actor=None):
    """Erase a person's assessments. Does not touch the access log.

    Returns what was removed and, explicitly, what was not — an erasure report that
    quietly omitted the retained access record would misrepresent what the subject
    was granted.
    """
    subject_id = str(subject_id)
    removed = {}

    for name, policy in SCHEDULE.items():
        if not policy["erasable_on_request"]:
            continue
        kept, dropped = [], 0
        for line in _rows(policy["path"]):
            try:
                entry = json.loads(line)
            except ValueError:
                kept.append(line)
                continue
            if str(entry.get("subject_id")) == subject_id:
                dropped += 1
            else:
                kept.append(line)
        if dropped and not dry_run:
            _rewrite(policy["path"], kept)
        removed[name] = dropped

    retained = {name: policy["holds"] for name, policy in SCHEDULE.items()
                if not policy["erasable_on_request"]}

    report = {
        "subject_id": subject_id,
        "dry_run": dry_run,
        "erased": removed,
        "retained": retained,
        "retained_because":
            "The access log records who viewed this person's record — evidence of "
            "official conduct, not an assessment of the subject. Erasing it on "
            "request would let anyone who improperly viewed a record destroy the "
            "proof by filing in the subject's name. It expires on the retention "
            "schedule instead.",
    }

    if not dry_run:
        try:
            import audit
            audit.record("erase_subject", "offender", subject_id,
                         extra={"erased": removed, "requested_by": actor})
        except Exception as e:      # never let the audit write undo the erasure
            print("ERASURE-AUDIT-FAILURE " + str(e), file=sys.stderr, flush=True)

    return report


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--purge", metavar="SINK", nargs="?", const="__all__")
    ap.add_argument("--erase", metavar="SUBJECT_ID")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it, nothing is written")
    args = ap.parse_args()

    if args.status or not (args.purge or args.erase):
        print(json.dumps(status(), indent=2, default=str))
        return
    if args.purge:
        sink = None if args.purge == "__all__" else args.purge
        print(json.dumps(purge(sink, dry_run=not args.apply), indent=2, default=str))
    if args.erase:
        print(json.dumps(erase_subject(args.erase, dry_run=not args.apply),
                         indent=2, default=str))


if __name__ == "__main__":
    main()
