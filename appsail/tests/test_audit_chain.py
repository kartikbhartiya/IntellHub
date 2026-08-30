"""Tampering tests for the audit log's hash chain.

The access-control story rests entirely on the audit trail: person-level lookups are
allowed *because* they leave a record of who looked and why. A record nobody can
check is a record nobody has to respect, so the chain has to be tested the way an
attacker would use it — by falsifying the log and asserting the check notices.

Each test here is one way a log gets falsified in practice. The truncation case is
the interesting one: a hash chain cannot detect its own truncation, because dropping
the newest lines leaves a shorter chain that still verifies perfectly. That gap was
found by running these attacks and is closed with an anchor outside the file.
"""
import hashlib
import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402


@pytest.fixture()
def log(tmp_path, monkeypatch):
    """A fresh keyed log with six entries, plus its untouched contents.

    The module is reimported under the fixture's environment because both the sink
    path and the chain key are read at import time.
    """
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "test-key-not-a-real-secret")

    import audit as audit_module
    audit = importlib.reload(audit_module)

    app = Flask(__name__)
    with app.test_request_context("/serverless/api/suspect/SUS-1001"):
        for i in range(6):
            audit.record("read_offender_dossier", "offender", f"SUS-100{i}",
                         reason="test")

    path = audit._PATH
    original = open(path, encoding="utf-8").read().splitlines()

    def rewrite(rows):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(rows) + "\n")

    yield audit, original, rewrite

    # Later tests import audit fresh; leave the module bound to the real sink.
    monkeypatch.undo()
    importlib.reload(audit_module)


def test_an_untouched_log_verifies(log):
    audit, original, _ = log
    result = audit.verify_chain()
    assert result["intact"] is True
    assert result["checked"] == 6
    assert result["keyed"] is True, "the fixture set a key; digests must be keyed"


def test_editing_which_record_was_opened_is_caught(log):
    """The obvious cover-up: change the subject so the log shows a different person
    than the one whose file was actually opened."""
    audit, original, rewrite = log
    rows = list(original)
    entry = json.loads(rows[3])
    entry["subject_id"] = "SUS-9999"
    rows[3] = json.dumps(entry, separators=(",", ":"))
    rewrite(rows)

    result = audit.verify_chain()
    assert result["intact"] is False
    assert result["broken_at"] == 4
    assert "digest" in result["reason"]


def test_reassigning_an_access_to_another_officer_is_caught(log):
    audit, original, rewrite = log
    rows = list(original)
    entry = json.loads(rows[2])
    entry["actor"] = "someone.else"
    rows[2] = json.dumps(entry, separators=(",", ":"))
    rewrite(rows)

    assert audit.verify_chain()["intact"] is False


def test_deleting_an_entry_is_caught(log):
    """Removing a line leaves the next entry pointing at a predecessor that is no
    longer there."""
    audit, original, rewrite = log
    rows = list(original)
    del rows[2]
    rewrite(rows)

    result = audit.verify_chain()
    assert result["intact"] is False
    assert "predecessor" in result["reason"]


def test_reordering_entries_is_caught(log):
    audit, original, rewrite = log
    rows = list(original)
    rows[1], rows[4] = rows[4], rows[1]
    rewrite(rows)

    assert audit.verify_chain()["intact"] is False


def test_truncating_the_tail_is_caught(log):
    """The attack a hash chain does not catch on its own.

    Dropping the newest lines leaves a shorter chain that verifies perfectly, which
    is exactly what someone would do after looking up a record they should not have.
    It is only detectable against an anchor outside the file — here the writer's own
    count of what it appended.
    """
    audit, original, rewrite = log
    rows = list(original)
    del rows[4:]
    rewrite(rows)

    result = audit.verify_chain()
    assert result["intact"] is False, "a truncated log must not pass as intact"
    # Sequence numbers, not a raw count: a rotated-away prefix legitimately shortens
    # the file, and comparing counts would report that as a truncated tail.
    assert result["last_sequence_seen"] == 3
    assert result["expected_entries"] == 6
    assert "removed from the end" in result["reason"]


def test_forging_an_entry_without_the_key_is_caught(log):
    """An attacker with file access but not AUDIT_HMAC_KEY can recompute a plain
    SHA-256 digest, but not the keyed one. This is what the key buys."""
    audit, original, rewrite = log
    rows = list(original)
    entry = json.loads(rows[3])
    entry["subject_id"] = "SUS-9999"
    body = json.dumps({k: v for k, v in entry.items() if k != "hash"},
                      separators=(",", ":"), sort_keys=True, default=str)
    entry["hash"] = hashlib.sha256(body.encode()).hexdigest()
    rows[3] = json.dumps(entry, separators=(",", ":"))
    rewrite(rows)

    assert audit.verify_chain()["intact"] is False


def test_the_chain_survives_a_restart(log, tmp_path, monkeypatch):
    """A restarted process must continue the existing chain rather than starting a
    second one from genesis, which would silently orphan everything written before."""
    audit, original, _ = log
    head_before = audit.verify_chain()["head"]

    import audit as audit_module
    reloaded = importlib.reload(audit_module)          # simulates a fresh process

    app = Flask(__name__)
    with app.test_request_context("/serverless/api/suspect/SUS-2001"):
        entry = reloaded.record("read_offender_dossier", "offender", "SUS-2001",
                                reason="after restart")

    assert entry["prev"] == head_before, "the chain restarted instead of continuing"
    assert entry["seq"] == 6, "sequence numbering restarted"
    assert reloaded.verify_chain()["intact"] is True


def test_the_integrity_note_states_what_the_chain_does_not_prove(log):
    """Overclaiming here is worse than the original honest disclaimer. The note must
    keep naming the two things the chain cannot do."""
    audit, _, _ = log
    note = audit.INTEGRITY_NOTE.lower()
    assert "truncation" in note
    assert "compromised application process" in note


def test_an_unkeyed_chain_says_so(tmp_path, monkeypatch):
    """Without a key the chain is still useful, but it must not claim more than it
    delivers: anyone with file access can recompute an unkeyed digest."""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "unkeyed.jsonl"))
    monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)

    import audit as audit_module
    audit = importlib.reload(audit_module)

    app = Flask(__name__)
    with app.test_request_context("/serverless/api/suspect/SUS-1001"):
        audit.record("read_offender_dossier", "offender", "SUS-1001", reason="test")

    result = audit.verify_chain()
    assert result["intact"] is True
    assert result["keyed"] is False
    assert "unkeyed" in result["note"]
    assert "AUDIT_HMAC_KEY is not set" in audit.INTEGRITY_NOTE

    monkeypatch.undo()
    importlib.reload(audit_module)


# --------------------------------------------------------- migrating an old log
def _write_unchained(path, count=3):
    """A log in the shape this system wrote before chaining existed."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for i in range(count):
            fh.write(json.dumps({
                "ts": "2026-08-01T00:00:0%d+00:00" % i,
                "actor": "old.user", "role": "investigator",
                "action": "read_offender_dossier", "subject_id": "SUS-%d" % i,
            }, separators=(",", ":")) + "\n")


def _fresh_audit(tmp_path, monkeypatch, name="audit.jsonl"):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / name))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "test-key-not-a-real-secret")
    import audit as audit_module
    return importlib.reload(audit_module)


def test_a_pre_chain_log_is_preserved_not_deleted(tmp_path, monkeypatch):
    """An unchained log cannot be continued — there is no digest for the next entry
    to point at — but it is still the record, so it must survive."""
    path = tmp_path / "audit.jsonl"
    _write_unchained(path)
    audit = _fresh_audit(tmp_path, monkeypatch)

    app = Flask(__name__)
    with app.test_request_context("/serverless/api/suspect/SUS-1001"):
        audit.record("read_offender_dossier", "offender", "SUS-1001", reason="test")

    retired = tmp_path / "audit.jsonl.pre-chain"
    assert retired.exists(), "the old entries were destroyed rather than retired"
    assert len(retired.read_text(encoding="utf-8").strip().splitlines()) == 3
    assert audit.verify_chain()["intact"] is True

    monkeypatch.undo()
    import audit as audit_module
    importlib.reload(audit_module)


def test_a_log_with_an_unchained_head_is_retired(tmp_path, monkeypatch):
    """Regression: retirement was decided from the last line, so a file whose head
    was unchained but whose tail was chained passed the check — and then failed every
    verification for the rest of its life, because verification walks from line 1."""
    path = tmp_path / "audit.jsonl"
    _write_unchained(path, count=2)

    audit = _fresh_audit(tmp_path, monkeypatch)
    app = Flask(__name__)
    with app.test_request_context("/serverless/api/suspect/SUS-1001"):
        audit.record("read_offender_dossier", "offender", "SUS-1001", reason="one")

    # Reload against the now-chained file: it must be treated as continuable.
    audit = _fresh_audit(tmp_path, monkeypatch)
    with app.test_request_context("/serverless/api/suspect/SUS-1002"):
        audit.record("read_offender_dossier", "offender", "SUS-1002", reason="two")

    result = audit.verify_chain()
    assert result["intact"] is True, result.get("reason")
    assert result["checked"] == 2, "the retired entries leaked into the new chain"

    monkeypatch.undo()
    import audit as audit_module
    importlib.reload(audit_module)


# ------------------------------------------------------------------ rotation
def test_verification_survives_a_log_rotation(log, monkeypatch):
    """Regression: verification read only the live file, so the first roll made the
    integrity endpoint return 409 forever.

    The chain continues across a roll by design — the new file's first entry points
    at the last entry of the rolled one — and a tamper-evidence control that is
    permanently red is worse than none, because it teaches whoever reads it to ignore
    the alarm.
    """
    audit, _, _ = log
    import os

    monkeypatch.setattr(audit, "_MAX_BYTES", 1)     # force a roll on the next write
    app = Flask(__name__)
    with app.test_request_context("/serverless/api/suspect/SUS-2001"):
        audit.record("read_offender_dossier", "offender", "SUS-2001", reason="rolled")
    monkeypatch.setattr(audit, "_MAX_BYTES", 8 * 1024 * 1024)
    with app.test_request_context("/serverless/api/suspect/SUS-2002"):
        audit.record("read_offender_dossier", "offender", "SUS-2002", reason="after")

    assert os.path.exists(audit._PATH + audit._ROTATE_SUFFIX), "no roll happened"

    result = audit.verify_chain()
    assert result["intact"] is True, result.get("reason")
    assert len(result["files_checked"]) == 2, "the rolled generation was not walked"
    assert result["checked"] == 8


def test_a_purged_prefix_is_reported_as_a_gap_not_as_tampering(log):
    """Retention removes whole rolled files on schedule, so a live log legitimately
    begins mid-chain. That must read as "history starts here, check the archive", not
    as a removed line — the two mean opposite things to whoever is reviewing."""
    audit, original, rewrite = log
    rewrite(original[3:])           # as if the earlier generation had been purged

    result = audit.verify_chain()

    # Everything still present verifies — nothing was altered — but the history no
    # longer starts at the beginning, and those are different facts. Reporting the
    # first as "broken" would cry wolf on every legitimate rotation; reporting the
    # second as fine would let a deleted prefix pass unremarked.
    assert result["intact"] is True, result.get("reason")
    assert result["history_complete"] is False
    assert result["sequence_range"][0] == 3
    assert "begins at entry 3" in result["note"]
    assert "archived copy" in result["note"]


def test_an_unrotated_log_reports_a_complete_history(log):
    audit, _, _ = log
    result = audit.verify_chain()
    assert result["intact"] is True
    assert result["history_complete"] is True
    assert result["sequence_range"] == [0, 5]
