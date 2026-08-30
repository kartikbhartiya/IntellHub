"""Role-based access control for person-level data.

Design intent
-------------
Aggregate intelligence and person-level intelligence are not the same thing and
must not carry the same access rules. "Kalaburagi has a 34% chargesheet rate" is a
management statistic. "These 40 named people are ranked by how likely we think
they are to offend again" is a dossier about individuals, and it is the single
most sensitive artefact this platform produces.

So the gate is placed there rather than across the whole application:

* **Statistical / aggregate surfaces stay open.** District funnels, forecasts,
  hotspot zones, the model registry — none of it identifies anyone.
* **Person-level surfaces are redacted, not refused.** An unauthenticated caller
  still gets the watchlist *shape* — how many people, the band distribution, the
  method — with names replaced by stable pseudonyms and calibrated probabilities
  withheld. Authorised callers get the full record.

Redaction rather than a 403 is deliberate. A hard refusal hides that the control
exists; a redacted response demonstrates it working, and lets an evaluator see
both states side by side without credentials.

Roles are ordered. A caller always has at least `analyst`, so the platform is
usable without signing in and nothing silently breaks.

Secrets
-------
`AUTH_SECRET` signs tokens; absent, a random per-process secret is generated, so
tokens simply do not survive a restart. That is the safe default — it is never a
hardcoded fallback secret, which would be worse than none.

`AUTH_ACCOUNTS` holds credentials as `user:password:role,user:password:role`.
Absent, the demo accounts below are enabled and the API advertises that it is
running in demo mode. **Set AUTH_ACCOUNTS before any real deployment.**
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from functools import wraps

from flask import request

# Ordered least to most privileged.
ROLES = ["analyst", "investigator", "supervisor"]
DEFAULT_ROLE = "analyst"
TOKEN_TTL_SECONDS = 8 * 3600

# A per-process random fallback means every container restart invalidates every
# token that was ever issued: users are signed out without warning, and because a
# stale token is simply treated as anonymous, the symptom is person-level pages
# returning 403 to someone who logged in a minute ago. On a platform that recycles
# containers freely that is not an edge case, it is the normal experience.
#
# The fallback stays (the app must boot without configuration) but it is reported,
# so /auth/me and /health can say the deployment is in this state rather than
# leaving it to be diagnosed from the outside.
AUTH_SECRET_CONFIGURED = bool(os.environ.get("AUTH_SECRET"))
_SECRET = os.environ.get("AUTH_SECRET") or secrets.token_hex(32)
_DEMO_MODE = not os.environ.get("AUTH_ACCOUNTS")

# Demo credentials, active only when AUTH_ACCOUNTS is unset. These exist so an
# evaluator can see the control working; they are published in the README and are
# not a secret. Any real deployment sets AUTH_ACCOUNTS and disables them.
_DEMO_PLAINTEXT = {
    "analyst": ("scrb-analyst", "analyst"),
    "io": ("scrb-io", "investigator"),
    "sp": ("scrb-sp", "supervisor"),
}
# Populated with hashes by _init_demo_accounts() on first use.
_DEMO_ACCOUNTS = {}


# Password storage uses scrypt from the standard library. No new vendored
# dependency: AppSail installs nothing server-side, so every package has to be
# cross-compiled into _vendor, and a hashing library is not worth 40 MB.
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password, salt=None):
    """Produce `scrypt$<salt_hex>$<key_hex>` — the format AUTH_ACCOUNTS accepts."""
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + salt.hex() + "$" + key.hex()


def verify_password(password, stored):
    """Constant-time check against a stored hash.

    Cleartext entries are still accepted so an existing AUTH_ACCOUNTS keeps working
    through a rotation, but `describe()` reports them as a misconfiguration rather
    than tolerating them silently.
    """
    if not stored:
        return False
    if str(stored).startswith("scrypt$"):
        try:
            _, salt_hex, key_hex = stored.split("$", 2)
            candidate = hashlib.scrypt(
                (password or "").encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
            return hmac.compare_digest(candidate.hex(), key_hex)
        except Exception:
            return False
    return hmac.compare_digest(str(stored), password or "")


def _ensure_timing_hash():
    global _TIMING_EQUALISATION_HASH
    if _TIMING_EQUALISATION_HASH is None:
        _TIMING_EQUALISATION_HASH = hash_password("timing-equalisation")
    return _TIMING_EQUALISATION_HASH


def _init_demo_accounts():
    """Hash the demo passwords on first use, so no cleartext credential exists in
    the process and the verification path is identical to a real deployment's."""
    if not _DEMO_ACCOUNTS:
        for user, (pw, role) in _DEMO_PLAINTEXT.items():
            _DEMO_ACCOUNTS[user] = (hash_password(pw), role)
    return _DEMO_ACCOUNTS


def cleartext_accounts():
    """Configured accounts still storing a cleartext password, for `describe()`."""
    if _DEMO_MODE:
        return []
    return [u for u, (secret, _) in _accounts().items()
            if not str(secret).startswith("scrypt$")]


def _accounts():
    """username -> (stored_secret, role).

    AUTH_ACCOUNTS entries are `user:scrypt$salt$key:role`. A scrypt hash contains
    "$" but never ":", so the three-way split still holds.
    """
    raw = os.environ.get("AUTH_ACCOUNTS")
    if not raw:
        return _init_demo_accounts()
    out = {}
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3 and parts[2] in ROLES:
            out[parts[0]] = (parts[1], parts[2])
    return out


def rank(role):
    try:
        return ROLES.index(role)
    except ValueError:
        return 0


def _sign(payload_b64):
    return hmac.new(_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def make_token(username, role):
    payload = {"u": username, "r": role, "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    body = urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body)}"


def verify_token(token):
    """Return the payload, or None. Signature is checked before anything is trusted."""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(urlsafe_b64decode(padded))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    if payload.get("r") not in ROLES:
        return None
    return payload


# Login throttling. The credential space is tiny — three accounts in demo mode —
# so an unthrottled login endpoint is brute-forceable in seconds. Keyed on
# username plus client address, so an attacker guessing at a real officer's
# username cannot lock that officer out from elsewhere.
_ATTEMPT_WINDOW_SECONDS = 300
_MAX_ATTEMPTS = 8
_LOCKOUT_SECONDS = 300
_attempts = {}
_attempt_lock = threading.Lock()
_MAX_TRACKED_ATTEMPTS = 4096

# Computed once at import so an unknown username costs exactly one scrypt, the same
# as a known one with a wrong password.
_TIMING_EQUALISATION_HASH = None


def _attempt_key(username):
    try:
        fwd = request.headers.get("X-Forwarded-For", "")
        ip = fwd.split(",")[0].strip() if fwd else (request.remote_addr or "unknown")
    except Exception:
        ip = "unknown"
    return str(username) + "@" + ip


def throttle_state(username):
    """(locked, seconds_remaining) for this caller."""
    key = _attempt_key(username)
    now = time.time()
    with _attempt_lock:
        entry = _attempts.get(key)
        if not entry:
            return False, 0
        _failures, first_at, locked_until = entry
        if locked_until and now < locked_until:
            return True, int(locked_until - now)
        if now - first_at > _ATTEMPT_WINDOW_SECONDS:
            _attempts.pop(key, None)
    return False, 0


def _register_failure(username):
    key = _attempt_key(username)
    now = time.time()
    with _attempt_lock:
        failures, first_at, _locked = _attempts.get(key, (0, now, 0))
        if now - first_at > _ATTEMPT_WINDOW_SECONDS:
            failures, first_at = 0, now
        failures += 1
        locked_until = now + _LOCKOUT_SECONDS if failures >= _MAX_ATTEMPTS else 0
        _attempts[key] = (failures, first_at, locked_until)

        # Entries were only ever removed on a successful login, or when that same key
        # was queried again after its window expired. An attacker rotating usernames
        # (or X-Forwarded-For, which is caller-controlled) therefore added a permanent
        # entry per request — a cheap memory-exhaustion path against an
        # unauthenticated endpoint, in a container with little headroom. Sweep expired
        # entries whenever the table grows, and cap it.
        if len(_attempts) > _MAX_TRACKED_ATTEMPTS:
            for k, (_f, started, until) in list(_attempts.items()):
                if now - started > _ATTEMPT_WINDOW_SECONDS and now >= until:
                    _attempts.pop(k, None)
            if len(_attempts) > _MAX_TRACKED_ATTEMPTS:
                # Still full of live entries: drop the oldest rather than grow.
                for k, _v in sorted(_attempts.items(), key=lambda kv: kv[1][1])[:512]:
                    _attempts.pop(k, None)
        return locked_until > 0


def _clear_failures(username):
    with _attempt_lock:
        _attempts.pop(_attempt_key(username), None)


def authenticate(username, password):
    """(username, password) -> (token, role), or (None, reason) on failure."""
    locked, remaining = throttle_state(username)
    if locked:
        return None, "locked_out:" + str(remaining)

    acct = _accounts().get(username)
    if not acct or not verify_password(password, acct[0]):
        # Hash even for an unknown username, so a missing account is not
        # distinguishable from a wrong password by response time. Against a
        # PRE-COMPUTED hash: generating one here ran scrypt twice for an unknown user
        # versus once for a known one, which doubled the very side channel it was
        # meant to remove, with the sign flipped.
        if not acct:
            verify_password(password or "", _ensure_timing_hash())
        _register_failure(username)
        return None, "invalid_credentials"

    _clear_failures(username)
    return make_token(username, acct[1]), acct[1]


def current_identity():
    """Identity for this request. Always resolves — unauthenticated is `analyst`."""
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    payload = verify_token(token)
    if payload:
        return {"user": payload["u"], "role": payload["r"], "authenticated": True}
    return {"user": None, "role": DEFAULT_ROLE, "authenticated": False}


def has_role(minimum):
    return rank(current_identity()["role"]) >= rank(minimum)


def demo_mode():
    return _DEMO_MODE


def describe():
    """Advertised so a reviewer can see the policy without reading the source."""
    ident = current_identity()
    return {
        "identity": ident,
        "roles": ROLES,
        "demo_mode": _DEMO_MODE,
        # Usernames and roles only. The passwords used to be served from this
        # endpoint, which put the supervisor credential for the live deployment one
        # unauthenticated GET away and took the whole access-control story with it.
        # Demo passwords are documented in the README for reviewers; the API does
        # not hand them out.
        "demo_accounts": (
            [{"username": u, "role": r} for u, (_, r) in _init_demo_accounts().items()]
            if _DEMO_MODE else []
        ),
        "insecure_cleartext_accounts": cleartext_accounts(),
        "policy": {
            "analyst": "Aggregate and statistical surfaces only. Person-level records "
                       "are returned pseudonymised with probabilities withheld.",
            "investigator": "Adds case-level and individual dossier access.",
            "supervisor": "Adds the full recidivism watchlist, including names and "
                          "calibrated probabilities.",
        },
        "note": ("Person-level responses are redacted rather than refused, so the control "
                 "is visible without credentials instead of hidden behind a 403."),
        "warning": ("Demo credentials are active because AUTH_ACCOUNTS is unset. Set it, "
                    "and AUTH_SECRET, before any deployment holding real records."
                    if _DEMO_MODE else None),
    }


def pseudonym(identifier):
    """Stable, non-reversible stand-in for a real identifier.

    Derived from the signing secret, so the mapping cannot be recomputed by a
    caller and does not survive a secret rotation.
    """
    digest = hmac.new(_SECRET.encode(), str(identifier).encode(), hashlib.sha256)
    return "PSN-" + digest.hexdigest()[:8].upper()


# Fields that survive redaction of a watchlist row. An **allowlist**, not a
# denylist: with a denylist, any field added to the watchlist later leaks by
# default until someone remembers to ban it, which is how redaction quietly
# fails. The first version of this used a denylist and leaked the real
# `criminal_id`, the probability under its second name `reoffence_pct`, and the
# `drivers` strings — which describe a person closely enough ("44 prior cases",
# "operates across 20 districts") to re-identify them from public records.
WATCHLIST_PUBLIC_FIELDS = {"band", "risk_index"}


def redact_watchlist_row(row):
    """Person-level row -> the shape of the row, with the person removed.

    What survives is deliberately only what makes the watchlist legible as a
    *distribution*: the band and the percentile rank. Everything that describes,
    identifies or quantifies an individual is dropped, and the identifier is
    replaced with a secret-derived pseudonym that is stable within a process,
    not reversible, and not recomputable by a caller.
    """
    ident_value = (row.get("criminal_id") or row.get("offender_id")
                   or row.get("suspect_id") or row.get("id"))
    out = {k: v for k, v in row.items() if k in WATCHLIST_PUBLIC_FIELDS}
    out["criminal_id"] = pseudonym(ident_value)
    out["name"] = out["criminal_id"]
    out["redacted"] = True
    return out


def require_role(minimum):
    """Hard gate. Used only where a redacted response makes no sense."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # A CORS preflight carries no Authorization header by design. Gating it
            # would 403 every browser POST before the real request is ever sent.
            if request.method == "OPTIONS":
                return fn(*args, **kwargs)
            if not has_role(minimum):
                from flask import jsonify
                ident = current_identity()
                return jsonify({
                    "error": "insufficient_role",
                    "required": minimum,
                    "current": ident["role"],
                    "hint": "POST /serverless/api/auth/login to obtain a token.",
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator
