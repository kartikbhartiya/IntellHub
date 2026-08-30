"""Tests for retention schedules and the erasure path.

The two that carry the argument:

* `test_erasure_does_not_touch_the_access_log` — the conflict between the erasure
  right and the audit chain, resolved deliberately rather than by accident. An access
  log a subject could delete would let anyone who improperly viewed a record destroy
  the proof by filing a request in that subject's name.
* `test_purging_the_audit_log_never_breaks_the_chain` — retention must not become the
  hole in tamper-evidence. Per-entry deletion would rewrite the chain, so the audit
  sink is enforced at whole-file granularity and the live file is left alone.
"""
import importlib
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import retention as retention_module  # noqa: E402

NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


def _write(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _entry(days_ago, subject="SUS-1", **extra):
    row = {"ts": (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"),
           "subject_id": subject}
    row.update(extra)
    return row


@pytest.fixture()
def sinks(tmp_path, monkeypatch):
    """Point every schedule at an isolated file."""
    retention = importlib.reload(retention_module)
    paths = {}
    for name in retention.SCHEDULE:
        path = str(tmp_path / f"{name}.jsonl")
        monkeypatch.setitem(retention.SCHEDULE[name], "path", path)
        paths[name] = path
    return retention, paths


# ------------------------------------------------------------------- schedule
def test_every_schedule_states_why(sinks):
    """A retention period without a justification is a number someone made up."""
    retention, _ = sinks
    for name, policy in retention.SCHEDULE.items():
        assert policy["days"] > 0, name
        assert len(policy["why"]) > 40, f"{name} has no real justification"
        assert policy["holds"], name


def test_status_counts_what_is_already_overdue(sinks):
    retention, paths = sinks
    _write(paths["predictions"], [_entry(10), _entry(900), _entry(1000)])

    row = retention.status()["predictions"]
    assert row["entries"] == 3
    assert row["past_retention"] == 2, "730-day schedule; two entries are older"


def test_status_admits_nothing_runs_on_a_timer(sinks):
    """A documented schedule that never executes is not a retention policy. The gap
    is stated rather than left for a reader to discover."""
    retention, _ = sinks
    assert "scheduler" in retention.status()["_note"]


# ---------------------------------------------------------------------- purge
def test_purge_defaults_to_a_preview(sinks):
    """Deleting personal data is correct and irreversible at the same time."""
    retention, paths = sinks
    _write(paths["predictions"], [_entry(900), _entry(10)])

    result = retention.purge("predictions", now=NOW)
    assert result["dry_run"] is True
    assert result["sinks"]["predictions"]["removed"] == 1
    assert len(open(paths["predictions"], encoding="utf-8").read().splitlines()) == 2


def test_purge_removes_only_what_is_past_retention(sinks):
    retention, paths = sinks
    _write(paths["predictions"], [_entry(900), _entry(10), _entry(800)])

    retention.purge("predictions", dry_run=False, now=NOW)
    rows = [json.loads(x) for x in
            open(paths["predictions"], encoding="utf-8").read().splitlines()]
    assert len(rows) == 1
    assert rows[0]["ts"].startswith("2026-08-14")


def test_an_unparseable_line_is_kept_not_destroyed(sinks):
    """A torn line is a reason to investigate, not to silently delete a record."""
    retention, paths = sinks
    with open(paths["predictions"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps(_entry(10)) + "\n")

    retention.purge("predictions", dry_run=False, now=NOW)
    assert len(open(paths["predictions"], encoding="utf-8").read().splitlines()) == 2


def test_purging_the_audit_log_never_breaks_the_chain(sinks):
    """Retention must not become the hole in tamper-evidence.

    Removing individual audit lines would break every digest after them, so the audit
    sink is enforced at whole-file granularity and the live file is never rewritten.
    """
    retention, paths = sinks
    live = paths["audit"]
    _write(live, [_entry(2000), _entry(10)])
    before = open(live, encoding="utf-8").read()

    result = retention.purge("audit", dry_run=False, now=NOW)
    assert result["sinks"]["audit"]["granularity"] == "whole-file"
    assert result["sinks"]["audit"]["live_file_untouched"] is True
    assert open(live, encoding="utf-8").read() == before, "the live chain was rewritten"


def test_a_rolled_audit_file_is_removed_only_when_entirely_overdue(sinks):
    """One recent line keeps the whole file: the alternative is deleting entries that
    are still within their retention period because they share a file with older ones.
    """
    retention, paths = sinks
    rolled = paths["audit"] + ".1"
    _write(rolled, [_entry(2000), _entry(5)])       # newest is recent

    retention.purge("audit", dry_run=False, now=NOW)
    assert os.path.exists(rolled), "a file containing a current entry was deleted"

    _write(rolled, [_entry(2000), _entry(1500)])    # every entry overdue
    retention.purge("audit", dry_run=False, now=NOW)
    assert not os.path.exists(rolled)


def test_an_unknown_sink_is_refused(sinks):
    retention, _ = sinks
    with pytest.raises(ValueError):
        retention.purge("nonexistent")


# -------------------------------------------------------------------- erasure
def test_erasure_removes_a_subjects_assessments(sinks):
    retention, paths = sinks
    _write(paths["predictions"], [_entry(10, "SUS-1"), _entry(10, "SUS-2")])
    _write(paths["outcomes"], [_entry(10, "SUS-1")])

    report = retention.erase_subject("SUS-1", dry_run=False)
    assert report["erased"]["predictions"] == 1
    assert report["erased"]["outcomes"] == 1

    remaining = [json.loads(x) for x in
                 open(paths["predictions"], encoding="utf-8").read().splitlines()]
    assert [r["subject_id"] for r in remaining] == ["SUS-2"]


def test_erasure_does_not_touch_the_access_log(sinks):
    """The deliberate limit.

    A log the subject could delete would let anyone who improperly viewed a record
    destroy the proof by filing an erasure request in that subject's name. The access
    log expires on its schedule instead, and the report says so rather than claiming
    a complete erasure.
    """
    retention, paths = sinks
    _write(paths["audit"], [_entry(10, "SUS-1", action="read_offender_dossier")])
    _write(paths["predictions"], [_entry(10, "SUS-1")])

    report = retention.erase_subject("SUS-1", dry_run=False)

    assert "audit" not in report["erased"]
    assert "audit" in report["retained"]
    assert "official conduct" in report["retained_because"]
    assert len(open(paths["audit"], encoding="utf-8").read().splitlines()) == 1


def test_erasure_defaults_to_a_preview(sinks):
    retention, paths = sinks
    _write(paths["predictions"], [_entry(10, "SUS-1")])

    report = retention.erase_subject("SUS-1")
    assert report["dry_run"] is True
    assert report["erased"]["predictions"] == 1
    assert len(open(paths["predictions"], encoding="utf-8").read().splitlines()) == 1


def test_erasing_an_unknown_subject_is_not_an_error(sinks):
    """A subject with no records still gets a truthful report rather than a failure."""
    retention, paths = sinks
    _write(paths["predictions"], [_entry(10, "SUS-2")])

    report = retention.erase_subject("SUS-NOBODY", dry_run=False)
    assert report["erased"]["predictions"] == 0
    assert len(open(paths["predictions"], encoding="utf-8").read().splitlines()) == 1


# ----------------------------------------------------------- timestamp shapes
def test_a_naive_timestamp_does_not_break_the_report(sinks):
    """Regression: `status()` compared each entry's timestamp against an aware `now`,
    so a single entry written without a timezone raised TypeError and the whole
    retention endpoint returned 500.

    Writers genuinely differ — `datetime.now(timezone.utc)` is aware, a timestamp
    carried over from source data is not — and a data-protection control cannot be
    one unusual log line away from being unable to report its own schedule.
    """
    retention, paths = sinks
    _write(paths["predictions"], [
        {"ts": "2025-01-01T00:00:00", "subject_id": "SUS-1"},          # naive
        {"ts": "2026-08-01T00:00:00+00:00", "subject_id": "SUS-2"},    # aware
    ])

    row = retention.status()["predictions"]
    assert row["entries"] == 2
    assert row["past_retention"] == 0
    assert row["oldest_entry"].startswith("2025-01-01")

    # And the same mixture must be purgeable rather than raising.
    result = retention.purge("predictions", now=NOW)
    assert result["sinks"]["predictions"]["removed"] == 0


def test_a_line_that_is_not_json_does_not_abort_the_report(sinks):
    """One torn line is survivable; losing the answer to "what do we hold, and for
    how long" is not."""
    retention, paths = sinks
    with io.open(paths["outcomes"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps({"ts": "2026-08-01T00:00:00+00:00", "subject_id": "S"}) + "\n")

    row = retention.status()["outcomes"]
    assert row["entries"] == 2
    assert row["oldest_entry"] is not None
