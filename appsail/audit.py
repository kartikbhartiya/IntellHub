"""Access audit trail for person-level data.

Why this exists
---------------
Before this module the platform had no audit trail of any kind. A repo-wide search
for `audit` returned exactly one hit — a subtitle string in the UI. That means a
supervisor could pull the full recidivism watchlist for every district in Karnataka
and there would be no evidence, anywhere, that it had happened.

For a police record system that is disqualifying on its own, and it is disqualifying
independently of whether the access control works. Access control decides who *may*
look; the audit trail is what makes anyone accountable for having looked. A system
that gates perfectly and logs nothing still cannot answer the only question that
matters after a leak: who read this person's file?

What is recorded
----------------
One JSON object per access to person-level data:

    ts          UTC timestamp, ISO-8601
    actor       authenticated username, or null
    role        the role the request was served at
    route       the Flask rule that matched
    action      what was done, e.g. "read_offender_dossier"
    subject     type + identifier of the *person or case* accessed — not just the
                endpoint. "GET /api/suspect" is useless six months later;
                "read_offender_dossier SUS-1042" is evidence.
    reason      the stated purpose, if the caller supplied one
    ip          best-effort client address
    result      ok | denied | error
    status      HTTP status returned

Where it goes
-------------
Two sinks, deliberately:

* **stdout**, prefixed `AUDIT `, as a single JSON line. AppSail's filesystem is
  ephemeral, so the container log is the only sink that survives a restart today.
* **a local JSONL file**, for development and for deployments with a real disk.

Neither is tamper-evident, and this module does not pretend otherwise — see
`INTEGRITY_NOTE`. A production deployment must ship these lines to an append-only
store outside the application's own trust boundary. What is fixed here is the
absence of a record; hardening where that record lives is a deployment decision.

Reason-for-access
-----------------
Real police record systems require an officer to state why they are opening a
file, because the log is only useful if it distinguishes legitimate case work from
browsing. Callers may supply `X-Access-Reason` (or `?reason=`). It is recorded, and
`reason_given: false` is recorded when it is absent, so unjustified access is
visible in review rather than indistinguishable. Setting `AUDIT_REQUIRE_REASON=1`
turns that from an observation into a hard requirement.
"""
import json
import os
import sys
import hashlib
import hmac
import threading
import time
from collections import deque
from datetime import datetime, timezone
from functools import wraps

from flask import request

import auth

_DIR = os.path.dirname(os.path.abspath(__file__))
_PATH = os.environ.get("AUDIT_LOG_PATH") or os.path.join(_DIR, "audit.jsonl")
_LOCK = threading.Lock()

# The file sink is append-only with no upstream rotation, and AppSail's disk is both
# ephemeral and small. Left unbounded, a browsing session over /registry/criminals and
# /suspect/<id> grows it until the disk fills — at which point open() raises, the
# handler prints AUDIT-SINK-FAILURE, and the only on-disk record is silently lost.
# One rolled generation is kept, which is enough to survive a restart; the durable
# record on this platform is stdout, and production forwards that to an append-only
# store outside the app.
_MAX_BYTES = 8 * 1024 * 1024
_ROTATE_SUFFIX = ".1"

# Recent entries kept in memory purely so /audit-log can serve something without a
# disk read. It is a convenience view over the real sinks, never the record itself.
_RECENT = deque(maxlen=2000)

REQUIRE_REASON = os.environ.get("AUDIT_REQUIRE_REASON", "").lower() in ("1", "true", "yes")

# Each entry carries its sequence number, the digest of the entry before it, and its
# own digest. Editing or deleting any line breaks every digest after it, so the log
# can be checked rather than trusted. With AUDIT_HMAC_KEY set the digests are keyed,
# which is what makes the chain hold against someone who can write to the file:
# without the key they cannot recompute it. Unkeyed, the chain still catches
# corruption, truncation and careless edits, but not a deliberate rewrite.
CHAIN_KEY = (os.environ.get("AUDIT_HMAC_KEY") or "").encode("utf-8")
_GENESIS = "0" * 64

INTEGRITY_NOTE = (
    "Entries are hash-chained: each carries the digest of its predecessor, so any "
    "edit, reorder or deletion is detectable via /audit-log/verify. "
    + ("Digests are HMAC-keyed, so the chain cannot be recomputed by an attacker "
       "holding only file access. "
       if CHAIN_KEY else
       "AUDIT_HMAC_KEY is not set, so digests are unkeyed: the chain detects "
       "corruption and careless edits, but someone with write access could "
       "recompute it. Set AUDIT_HMAC_KEY in any deployment holding real records. ")
    + "The chain proves no entry was altered, reordered or removed from the middle. "
    "It cannot by itself detect truncation of the newest entries, since a shortened "
    "chain still verifies; that is checked against the writer's own counter while "
    "the process lives, and against the forwarded stdout copy across restarts. "
    "Nor does it protect against a compromised application process, which can still "
    "write false entries. "
    "A deployment holding real records must also forward these lines to an "
    "append-only store outside this application's trust boundary."
)

# Chain head. Recovered from the existing file on first write so a restart continues
# the chain instead of silently starting a second one.
#
# SINGLE WRITER. The chain is only coherent while one process appends to the file.
# `_LOCK` serialises the eight gunicorn threads, which is why `app-config.json` pins
# `--workers 1 --threads 8`; a second worker process would keep its own head and the
# two would interleave, breaking verification on every other line. Raising the worker
# count means moving the chain behind a shared sequencer first.
_chain = {"seq": 0, "prev": _GENESIS, "loaded": False}


def _digest(payload):
    """Digest of one canonical entry body."""
    raw = payload.encode("utf-8")
    if CHAIN_KEY:
        return hmac.new(CHAIN_KEY, raw, hashlib.sha256).hexdigest()
    return hashlib.sha256(raw).hexdigest()


def _body(entry):
    """Canonical bytes a digest is taken over: the entry minus the digest itself,
    key order fixed so the value does not depend on dict insertion order."""
    return json.dumps({k: v for k, v in entry.items() if k != "hash"},
                      separators=(",", ":"), sort_keys=True, default=str)


def _load_chain_head():
    """Continue the chain from the last line on disk. Caller holds _LOCK."""
    if _chain["loaded"]:
        return
    _chain["loaded"] = True
    try:
        first = last = None
        with open(_PATH, "rb") as fh:
            for line in fh:                     # read once, at startup only
                if first is None:
                    first = line
                last = line
        if last:
            # Verification walks from line 1, so the *first* entry decides whether
            # this file can ever verify. Judging by the tail alone accepted a file
            # whose head was unchained, which then failed every check for the rest of
            # its life with no path back.
            head_chained = False
            try:
                head_chained = bool(json.loads(first.decode("utf-8")).get("hash"))
            except (ValueError, TypeError, AttributeError):
                head_chained = False

            prior = json.loads(last.decode("utf-8"))
            if head_chained and prior.get("hash"):
                _chain["seq"] = int(prior.get("seq", 0)) + 1
                _chain["prev"] = prior["hash"]
            else:
                # A log from before chaining. It cannot be continued — there is no
                # digest for the next entry to point at — and it must not be
                # deleted, because it is still the record. Roll it aside once and
                # start a chain that verifies cleanly from its first entry.
                _retire_unchained_log()
    except (OSError, ValueError, TypeError):
        # No readable tail: start a fresh chain. verify_chain reports the
        # discontinuity rather than this pretending the history was intact.
        pass


def _retire_unchained_log():
    """Preserve a pre-chain log under .pre-chain and begin a fresh chain."""
    retired = _PATH + ".pre-chain"
    try:
        if os.path.exists(retired):
            os.remove(retired)
        os.replace(_PATH, retired)
        print("AUDIT-CHAIN-MIGRATION existing unchained log preserved at "
              + retired + "; a new chain starts from this entry",
              file=sys.stderr, flush=True)
    except OSError as e:
        print("AUDIT-CHAIN-MIGRATION-FAILURE " + str(e), file=sys.stderr, flush=True)


def verify_chain(path=None, limit=None):
    """Recompute the chain and report the first entry that disagrees.

    This is the point of the chain: an answer to "has this log been edited since it
    was written" that does not require trusting the process that wrote it.

    The rolled generation is walked first, so a rotation does not read as tampering.
    """
    live = path or _PATH
    # Oldest first, so the walk follows the order the entries were written.
    files = [f for f in (live + _ROTATE_SUFFIX, live) if os.path.exists(f)]
    if not files:
        return {"checked": 0, "intact": True, "history_complete": True, "keyed": bool(CHAIN_KEY),
                "note": "no log file yet"}

    prev, count = None, 0
    first_seq = last_seq = None
    expected_seq = None

    def broken(source, lineno, reason):
        return {"checked": count, "intact": False,
                "broken_at": lineno, "broken_in": os.path.basename(source),
                "reason": reason, "keyed": bool(CHAIN_KEY)}

    for source in files:
        try:
            fh = open(source, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                if limit and count >= limit:
                    break
                try:
                    entry = json.loads(line)
                except ValueError:
                    return broken(source, lineno, "line is not valid JSON")

                claimed = entry.get("hash")
                if claimed is None:
                    return broken(source, lineno,
                                  "entry carries no digest - a log written before "
                                  "chaining is retired to .pre-chain on the next "
                                  "write rather than continued")

                seq = int(entry.get("seq", 0))
                if prev is None:
                    # The first entry seen. It need not be entry zero: everything
                    # before it may have been rotated away or purged on schedule.
                    first_seq = seq
                elif entry.get("prev") != prev:
                    return broken(source, lineno,
                                  "entry does not follow its predecessor - a line "
                                  "was removed, reordered or inserted, or a second "
                                  "process appended concurrently (the chain requires "
                                  "a single writer)")
                elif expected_seq is not None and seq != expected_seq:
                    return broken(source, lineno, "sequence number skips")

                if _digest(_body(entry)) != claimed:
                    # Do not assert tampering when a changed key produces exactly the
                    # same symptom. An edited entry breaks its own digest AND the next
                    # entry's `prev`; a rotated AUDIT_HMAC_KEY breaks every digest from
                    # that point while the `prev` links still chain perfectly. Reaching
                    # here with an intact link is therefore ambiguous, and naming only
                    # the accusation would be a false one — which for an access log is
                    # the worst possible direction to be wrong in.
                    linked = entry.get("prev") == prev
                    return broken(source, lineno,
                                  "entry contents do not match its digest - this line "
                                  "was altered after it was written"
                                  + (", OR AUDIT_HMAC_KEY changed since it was written "
                                     "(the chain links are intact here, which a "
                                     "content edit would also have broken - both causes "
                                     "look identical from inside the file)"
                                     if linked else ""))
                prev = claimed
                last_seq = seq
                expected_seq = seq + 1
                count += 1

    if count == 0:
        return {"checked": 0, "intact": True, "history_complete": True, "keyed": bool(CHAIN_KEY),
                "note": "log file is empty"}

    # A chain cannot detect its own truncation: drop the last N lines and what
    # remains still verifies. The writer's sequence counter is an anchor outside the
    # file, so a short log is caught for as long as this process lives. Comparing
    # sequence numbers rather than a raw count is what keeps a rotated-away prefix
    # from being reported as a truncated tail.
    written = _chain["seq"] if _chain["loaded"] else None
    if written is not None and not limit and last_seq + 1 < written:
        return {"checked": count, "intact": False,
                "broken_at": count + 1, "keyed": bool(CHAIN_KEY),
                "expected_entries": written, "last_sequence_seen": last_seq,
                "reason": "the log ends before the last entry that was written - "
                          f"{written - last_seq - 1} entry(s) were removed from the "
                          "end"}

    # Two separate questions, and conflating them is what made the endpoint useless
    # after a rotation:
    #   intact           — has anything present been altered, reordered or removed?
    #   history_complete — am I looking at the whole history, or does it start late?
    # A file that begins at entry 400 can be perfectly intact. Whether the first 400
    # were rotated away on schedule or deleted by someone is not a question this
    # function can answer, so it reports the gap instead of guessing.
    result = {"checked": count, "intact": True,
              "history_complete": not first_seq,
              "head": prev,
              "keyed": bool(CHAIN_KEY),
              "files_checked": [os.path.basename(f) for f in files],
              "sequence_range": [first_seq, last_seq],
              "truncation_anchor": "writer counter" if written is not None
                                   else "none in this process - compare against the "
                                        "forwarded stdout copy"}
    notes = []
    if first_seq:
        notes.append(
            f"Every entry present verifies, but the visible history begins at entry "
            f"{first_seq}: the first {first_seq} are not in these files. That is "
            "expected after a rotation or a retention purge and must be confirmed "
            "against the archived copy - this check cannot tell the two apart.")
    if not CHAIN_KEY:
        notes.append("digests are unkeyed - see integrity note")
    if notes:
        result["note"] = " ".join(notes)
    return result


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def stated_reason():
    """The caller's stated purpose for this access, if any."""
    return (request.headers.get("X-Access-Reason")
            or request.args.get("reason")
            or "").strip() or None


def record(action, subject_type=None, subject_id=None, result="ok",
           status=200, reason=None, extra=None):
    """Write one audit entry. Never raises — a failed log must not fail a request,
    but it is itself reported to stdout so silent loss is visible."""
    try:
        ident = auth.current_identity()
    except Exception:
        ident = {"user": None, "role": None, "authenticated": False}

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": ident.get("user"),
        "role": ident.get("role"),
        "authenticated": bool(ident.get("authenticated")),
        "route": getattr(request.url_rule, "rule", request.path) if request else None,
        "method": request.method if request else None,
        "action": action,
        "subject_type": subject_type,
        "subject_id": str(subject_id) if subject_id is not None else None,
        "reason": reason,
        "reason_given": bool(reason),
        "ip": _client_ip() if request else None,
        "result": result,
        "status": status,
    }
    if extra:
        entry.update(extra)

    # The entry is not serialised yet: the chain fields are added under the lock
    # below and have to be inside the bytes that get hashed and written.
    with _LOCK:
        # The chain fields are assigned under the lock because their value depends on
        # the entry written immediately before this one; computing them outside would
        # let two threads claim the same sequence number.
        _load_chain_head()
        entry["seq"] = _chain["seq"]
        entry["prev"] = _chain["prev"]
        entry["hash"] = _digest(_body(entry))
        _chain["seq"] += 1
        _chain["prev"] = entry["hash"]

        line = json.dumps(entry, separators=(",", ":"), default=str)
        _RECENT.append(entry)
        try:
            _rotate_if_needed()
            with open(_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + chr(10))
        except Exception as e:
            # Losing the file sink is survivable (stdout is the durable one on
            # AppSail) but must not be silent.
            print("AUDIT-SINK-FAILURE " + str(e), file=sys.stderr, flush=True)

    # stdout is the durable sink on this platform, and carries the same chained bytes
    # as the file. Written after the lock is released — print() is atomic per call, so
    # lines never tear, and holding the lock across it would serialise all eight
    # gunicorn threads on the hot path. Two threads can therefore emit to stdout out
    # of sequence order; every line carries its own `seq`, so a consumer sorts by that
    # rather than trusting arrival order. The file is written in order under the lock.
    try:
        print("AUDIT " + line, file=sys.stdout, flush=True)
    except Exception:
        pass
    return entry


def _rotate_if_needed():
    """Roll the sink once it passes the cap. Caller holds _LOCK."""
    try:
        if os.path.getsize(_PATH) < _MAX_BYTES:
            return
    except OSError:
        return          # not created yet
    rolled = _PATH + _ROTATE_SUFFIX
    try:
        if os.path.exists(rolled):
            os.remove(rolled)
        os.replace(_PATH, rolled)
        # The chain deliberately continues across the roll rather than restarting:
        # the new file's first entry still points at the last entry of the rolled
        # one, so `verify_chain` walks the rolled generation first and the seam is
        # not mistaken for a removed line.
    except OSError as e:
        print("AUDIT-ROTATE-FAILURE " + str(e), file=sys.stderr, flush=True)


def audited(action, subject_type=None, subject_arg=None):
    """Record every call to a person-level route.

    `subject_arg` names the view argument holding the identifier of the person or
    case being accessed, so the entry says *whose* record was read rather than
    merely which endpoint was hit.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if request.method == "OPTIONS":
                return fn(*args, **kwargs)   # a preflight is not an access
            subject_id = kwargs.get(subject_arg) if subject_arg else None
            reason = stated_reason()

            if REQUIRE_REASON and not reason:
                from flask import jsonify
                record(action, subject_type, subject_id, result="denied",
                       status=400, reason=None)
                return jsonify({
                    "error": "reason_required",
                    "hint": "Supply an X-Access-Reason header stating why this record "
                            "is being accessed.",
                }), 400

            started = time.perf_counter()
            try:
                response = fn(*args, **kwargs)
            except Exception:
                record(action, subject_type, subject_id, result="error",
                       status=500, reason=reason)
                raise

            status = 200
            if isinstance(response, tuple) and len(response) > 1:
                try:
                    status = int(response[1])
                except (TypeError, ValueError):
                    status = 200

            record(action, subject_type, subject_id,
                   result="ok" if status < 400 else "denied",
                   status=status, reason=reason,
                   extra={"ms": round((time.perf_counter() - started) * 1000, 1)})
            return response
        return wrapper
    return decorator


def recent(limit=200, actor=None, subject_id=None):
    """Most recent entries, newest first. A view for review, not the record."""
    rows = list(_RECENT)
    if actor:
        rows = [r for r in rows if r.get("actor") == actor]
    if subject_id:
        rows = [r for r in rows if r.get("subject_id") == str(subject_id)]
    return rows[::-1][:limit]


def summary():
    """Shape of the log, for the review endpoint."""
    rows = list(_RECENT)
    by_actor, by_action, unjustified = {}, {}, 0
    for r in rows:
        actor = r.get("actor") or "anonymous"
        by_actor[actor] = by_actor.get(actor, 0) + 1
        by_action[r.get("action")] = by_action.get(r.get("action"), 0) + 1
        if not r.get("reason_given"):
            unjustified += 1
    return {
        "entries_in_memory": len(rows),
        "memory_capacity": _RECENT.maxlen,
        "by_actor": by_actor,
        "by_action": by_action,
        "accesses_without_stated_reason": unjustified,
        "reason_required": REQUIRE_REASON,
        "file_sink": _PATH,
        "file_max_bytes": _MAX_BYTES,
        "integrity": INTEGRITY_NOTE,
        "chain": {
            "entries_written": _chain["seq"],
            "head": _chain["prev"],
            "keyed": bool(CHAIN_KEY),
        },
    }
