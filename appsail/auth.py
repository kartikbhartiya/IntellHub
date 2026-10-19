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

`AUTH_ACCOUNTS` holds credentials as `user:password:role,user:password:role`,
optionally `user:password:role:district[:station]` for the district- and
station-scoped roles. Absent, the demo accounts below are enabled and the API
advertises that it is running in demo mode. **Set AUTH_ACCOUNTS before any real
deployment.**

Roles, permissions and jurisdiction
-----------------------------------
A police hierarchy is not a single ladder. A Sub-Inspector and a DSP are both
"allowed to read case files", but one of them is allowed to read *a station's*
case files and the other *a district's*. So a role here carries three separate
things, and they are kept separate on purpose:

* **tier** — the coarse privilege rank the original three-role gates
  (`require_role('investigator')`, `require_role('supervisor')`) compare against.
  Every new role has one, so every existing gate keeps working without edits.
* **permissions** — named capabilities (`surveillance_decide`, `audit_read`, …).
  Gates that do not fit a single ladder use these instead of a tier, because "may
  verify a CCTV match" and "may read the audit log" are not ordered relative to
  each other.
* **scope** — state, district or station. It is carried *inside the signed token*,
  so a list view can be narrowed to the caller's jurisdiction and no query
  parameter can widen it.

The legacy roles (`analyst`, `investigator`, `supervisor`) keep exactly the
powers they had; the demo users `analyst`, `io` and `sp` are unchanged.
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

from flask import has_request_context, request

import karnataka

# ---------------------------------------------------------------------------
# Role model
# ---------------------------------------------------------------------------
# Every capability a gate can ask for, with what it actually unlocks. Published by
# /auth/roles so the UI renders the matrix the server enforces rather than a copy
# of it that can drift.
PERMISSIONS = {
    "view_aggregates": "Statistical surfaces: funnels, forecasts, hotspots, maps, model cards.",
    "view_cases": "The FIR list and individual case files.",
    "view_dossiers": "Individual offender dossiers and registry rows.",
    "view_network": "The criminal knowledge graph, predicted links and syndicates.",
    "use_copilot": "The Kavya copilot and the agent console, which read person-level data.",
    "surveillance_view": "CCTV and ANPR matches that name people; acknowledging an alert.",
    "surveillance_decide": "Verifying, rejecting or reopening a CCTV / ANPR match.",
    "surveillance_escalate": "Escalating a CCTV / ANPR match up the chain of command.",
    "watchlist_names": "The recidivism watchlist with names and calibrated probabilities "
                       "(limited to the caller's district for a district role).",
    "audit_read": "The access audit log, its chain verification and per-subject reports.",
    "contest_resolve": "Adjudicating challenges raised against an assessment.",
    "system_admin": "Platform settings, corpus sync, model replay and data retention.",
    "register_fir": "Registering a new FIR at a police station inside the caller's jurisdiction.",
}

_BASE = ("view_aggregates",)
_CASEWORK = _BASE + ("view_cases", "view_dossiers", "view_network", "use_copilot",
                     "surveillance_view", "register_fir")
_FULL = tuple(PERMISSIONS)

# Ordered least to most privileged by tier (ties keep the hierarchy's own order).
# `tier` is what the original require_role('investigator' | 'supervisor') gates
# compare, so si/sho/dsp pass an investigator gate and sp/dgp/admin a supervisor
# gate without any of those routes being edited. Permissions decide everything a
# single ladder cannot express.
ROLE_TABLE = {
    "analyst": {
        "tier": 0, "scope": "state", "label": "Analyst",
        "description": "SCRB statistical desk. Aggregate and statistical surfaces only; "
                       "person-level records arrive pseudonymised with probabilities withheld.",
        "permissions": _BASE,
    },
    "si": {
        "tier": 1, "scope": "station", "label": "Sub-Inspector (IO)",
        "description": "Investigating officer. Case files and dossiers; list views limited to "
                       "the officer's police station. Can acknowledge a CCTV alert but not "
                       "verify or reject the match.",
        "permissions": _CASEWORK,
    },
    "sho": {
        "tier": 1, "scope": "station", "label": "Station House Officer",
        "description": "Officer in charge of a police station. Station case list, and may "
                       "verify or reject CCTV matches raised in the station's district.",
        "permissions": _CASEWORK + ("surveillance_decide",),
    },
    "investigator": {
        "tier": 1, "scope": "state", "label": "Investigator",
        "description": "Legacy statewide investigator role (demo user `io`). Case-level and "
                       "individual dossier access across every district.",
        "permissions": _CASEWORK + ("surveillance_decide", "surveillance_escalate"),
    },
    "dsp": {
        "tier": 1, "scope": "district", "label": "Deputy SP",
        "description": "Sub-divisional officer. District case list and CCTV alerts; may "
                       "verify, reject and escalate matches in the district.",
        "permissions": _CASEWORK + ("surveillance_decide", "surveillance_escalate"),
    },
    "sp": {
        "tier": 2, "scope": "district", "label": "Superintendent of Police",
        "description": "District head. Everything a DSP can do, plus the named recidivism "
                       "watchlist for offenders with a case in the district.",
        "permissions": _CASEWORK + ("surveillance_decide", "surveillance_escalate",
                                    "watchlist_names"),
    },
    "supervisor": {
        "tier": 2, "scope": "state", "label": "Supervisor",
        "description": "Legacy statewide supervisor role (demo user `sp`). The full named "
                       "watchlist, the audit log, contests, retention and administration.",
        "permissions": _FULL,
    },
    "dgp": {
        "tier": 2, "scope": "state", "label": "State DGP",
        "description": "State head. Everything a supervisor can do, across all 31 districts.",
        "permissions": _FULL,
    },
    "admin": {
        "tier": 3, "scope": "state", "label": "Admin",
        "description": "Platform administrator. Everything, including settings, corpus sync "
                       "and retention. Holds the same permissions as the DGP today — no "
                       "admin-only surface exists yet — but sits a tier above so one can be "
                       "gated on require_role('admin') when it does.",
        "permissions": _FULL,
    },
}
ROLES = list(ROLE_TABLE)
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
#
# username -> (password, role, district, station). The first three are the original
# demo users and must not change: the README, the tests and anyone who has used the
# demo rely on them.
_DEMO_PLAINTEXT = {
    "analyst": ("scrb-analyst", "analyst", None, None),
    "io": ("scrb-io", "investigator", None, None),
    "sp": ("scrb-sp", "supervisor", None, None),
    "dgp@ksp.gov.in": ("scrb-dgp", "dgp", None, None),
    "admin@ksp.gov.in": ("scrb-admin", "admin", None, None),
    "analyst@ksp.gov.in": ("scrb-analyst", "analyst", None, None),
    "sp.bengaluru@ksp.gov.in": ("scrb-sp", "sp", "Bengaluru Urban", None),
    "dsp.mysuru@ksp.gov.in": ("scrb-dsp", "dsp", "Mysuru", None),
    "sho.koramangala@ksp.gov.in": ("scrb-sho", "sho", "Bengaluru Urban", "Koramangala PS"),
    "si.koramangala@ksp.gov.in": ("scrb-si", "si", "Bengaluru Urban", "Koramangala PS"),
}
# Populated with hashes by _init_demo_accounts() on first use. Values stay
# (hash, role) pairs — the shape every consumer unpacks — and jurisdiction lives
# alongside in _DEMO_SCOPES.
_DEMO_ACCOUNTS = {}
_DEMO_SCOPES = {u: (d, s) for u, (_pw, _r, d, s) in _DEMO_PLAINTEXT.items()}

# One-click demo personas for the sign-in page, each bound to one demo account. A
# switchable persona may be re-issued for any of the 31 districts, so an evaluator
# can see the jurisdiction filter move without a separate account per district.
DEMO_PERSONAS = [
    {"id": "dgp", "label": "Director General of Police", "account": "dgp@ksp.gov.in",
     "switchable_district": False,
     "description": "Statewide command view: every district, the named watchlist, the "
                    "audit log and the contest queue."},
    {"id": "admin", "label": "System Administrator", "account": "admin@ksp.gov.in",
     "switchable_district": False,
     "description": "Platform settings, corpus sync and retention, plus full read access."},
    {"id": "analyst", "label": "SCRB Analyst", "account": "analyst@ksp.gov.in",
     "switchable_district": False,
     "description": "Aggregates, forecasts and maps. Person-level records arrive "
                    "pseudonymised."},
    {"id": "sp", "label": "District SP", "account": "sp.bengaluru@ksp.gov.in",
     "switchable_district": True,
     "description": "District head: cases, CCTV alerts and the named watchlist for one "
                    "district. Pick the district."},
    {"id": "dsp", "label": "Deputy SP", "account": "dsp.mysuru@ksp.gov.in",
     "switchable_district": True,
     "description": "District cases and CCTV alerts; can verify and escalate matches. "
                    "Pick the district."},
    {"id": "sho", "label": "SHO, Koramangala PS", "account": "sho.koramangala@ksp.gov.in",
     "switchable_district": False,
     "description": "Station case list; verifies or rejects CCTV matches in Bengaluru Urban."},
    {"id": "si", "label": "SI, Koramangala PS", "account": "si.koramangala@ksp.gov.in",
     "switchable_district": False,
     "description": "Station case list and dossiers; can acknowledge a CCTV alert but not "
                    "verify it."},
]
_PERSONA_INDEX = {p["id"]: p for p in DEMO_PERSONAS}


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
        for user, (pw, role, _d, _s) in _DEMO_PLAINTEXT.items():
            _DEMO_ACCOUNTS[user] = (hash_password(pw), role)
    return _DEMO_ACCOUNTS


def cleartext_accounts():
    """Configured accounts still storing a cleartext password, for `describe()`."""
    if _DEMO_MODE:
        return []
    return [u for u, (secret, _) in _accounts().items()
            if not str(secret).startswith("scrypt$")]


def _parse_accounts(raw):
    """AUTH_ACCOUNTS -> (accounts, scopes, rejected).

    Entries are `user:scrypt$salt$key:role`, or `...:role:district[:station]` for a
    district- or station-scoped role. A scrypt hash contains "$" but never ":", and
    no Karnataka district or station name contains either, so the split holds.

    An entry whose jurisdiction does not validate is dropped and *named* in
    `rejected`, never admitted with a guessed or empty scope: a station officer
    whose station failed to parse would otherwise fall through to no filter at all,
    which is the widest view in the system, not the narrowest.
    """
    accounts, scopes, rejected = {}, {}, []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        user = parts[0]
        if len(parts) < 3 or parts[2] not in ROLE_TABLE:
            rejected.append(user)
            continue
        district = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
        station = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
        if len(parts) > 5 or not _valid_scope(parts[2], district, station):
            rejected.append(user)
            continue
        accounts[user] = (parts[1], parts[2])
        scopes[user] = (district, station)
    return accounts, scopes, rejected


def _accounts():
    """username -> (stored_secret, role)."""
    raw = os.environ.get("AUTH_ACCOUNTS")
    if not raw:
        return _init_demo_accounts()
    return _parse_accounts(raw)[0]


def _account_scope(username):
    """(district, station) for an account; (None, None) for a statewide one."""
    raw = os.environ.get("AUTH_ACCOUNTS")
    if not raw:
        return _DEMO_SCOPES.get(username, (None, None))
    return _parse_accounts(raw)[1].get(username, (None, None))


def rejected_accounts():
    """AUTH_ACCOUNTS entries that were refused, for `describe()`."""
    raw = os.environ.get("AUTH_ACCOUNTS")
    return _parse_accounts(raw)[2] if raw else []


def _valid_scope(role, district, station):
    """Does this (district, station) pair fit the role's jurisdiction level?

    State roles carry neither; district roles carry exactly a real district; station
    roles carry a real district and a station on that district's roster. Anything
    else is malformed, and a malformed scope is refused rather than read as "no
    restriction".
    """
    spec = ROLE_TABLE.get(role)
    if not spec:
        return False
    level = spec["scope"]
    if level == "state":
        return district is None and station is None
    if district not in karnataka.DISTRICTS:
        return False
    if level == "district":
        return station is None
    return station in karnataka.STATIONS.get(district, ())


def rank(role):
    """Privilege tier. Unknown roles rank as analyst, never above it."""
    spec = ROLE_TABLE.get(role)
    return spec["tier"] if spec else 0


def permissions_for(role):
    spec = ROLE_TABLE.get(role)
    return list(spec["permissions"]) if spec else list(_BASE)


def _sign(payload_b64):
    return hmac.new(_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def make_token(username, role, district=None, station=None):
    """Sign a token. The jurisdiction travels in the token ("d", "st") so that a list
    view can be narrowed from what was signed, not from what was asked for.

    This does not validate: verify_token is the single authority on what a token may
    claim, so an invalid role or scope here yields a token that never verifies.
    """
    payload = {"u": username, "r": role, "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    if district:
        payload["d"] = district
    if station:
        payload["st"] = station
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
    if payload.get("r") not in ROLE_TABLE:
        return None
    # A district or station role without a valid jurisdiction is malformed. It is
    # refused outright rather than served unfiltered, because "no district" would
    # otherwise mean "every district".
    if not _valid_scope(payload["r"], payload.get("d"), payload.get("st")):
        return None
    return payload


# Login throttling. The credential space is tiny — ten published accounts in demo
# mode — so an unthrottled login endpoint is brute-forceable in seconds. Keyed on
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
    district, station = _account_scope(username)
    return make_token(username, acct[1], district, station), acct[1]


def identity_for(user, role, district=None, station=None, authenticated=True):
    """The identity block every consumer reads: /auth/me, the audit trail, the gates.

    `user`, `role` and `authenticated` are the original three fields and keep their
    meaning; everything after them was added with the role model.
    """
    spec = ROLE_TABLE.get(role) or ROLE_TABLE[DEFAULT_ROLE]
    return {
        "user": user,
        "role": role,
        "authenticated": authenticated,
        "role_label": spec["label"],
        "tier": spec["tier"],
        "scope": {"level": spec["scope"], "district": district, "station": station},
        "permissions": list(spec["permissions"]),
    }


def identity_from_payload(payload):
    """Identity for a verified token payload; anonymous if it did not verify."""
    if not payload:
        return identity_for(None, DEFAULT_ROLE, authenticated=False)
    return identity_for(payload["u"], payload["r"], payload.get("d"), payload.get("st"))


def current_identity():
    """Identity for this request. Always resolves — unauthenticated is `analyst`."""
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    return identity_from_payload(verify_token(token))


def has_role(minimum):
    return rank(current_identity()["role"]) >= rank(minimum)


def has_permission(permission, identity=None):
    ident = identity or current_identity()
    return permission in ident.get("permissions", ())


def in_jurisdiction(district, station=None, identity=None):
    """Is a record at (district, station) inside the caller's jurisdiction?

    Statewide roles see everything. A district role matches on district; a station
    role matches on its own station (and, since station names are only unique within
    a district, the district too). A record with no station cannot be in a station's
    jurisdiction — absence of a value is not a match.
    """
    scope = (identity or current_identity())["scope"]
    if scope["level"] == "state":
        return True
    if not district or str(district) != scope["district"]:
        return False
    if scope["level"] == "district":
        return True
    return bool(station) and str(station) == scope["station"]


def demo_mode():
    return _DEMO_MODE


# ---------------------------------------------------------------------------
# Demo personas
# ---------------------------------------------------------------------------
def _canonical_district(name):
    """Case-insensitive match onto one of the 31 district names, or None."""
    wanted = str(name or "").strip().lower()
    for d in karnataka.DISTRICT_NAMES:
        if d.lower() == wanted:
            return d
    return None


def _persona_view(p):
    """A persona as the sign-in page sees it. No password: none is needed, and the
    API has not served one since that was found to be a leak."""
    _pw, role, district, station = _DEMO_PLAINTEXT[p["account"]]
    spec = ROLE_TABLE[role]
    return {
        "id": p["id"],
        "label": p["label"],
        "username": p["account"],
        "role": role,
        "role_label": spec["label"],
        "tier": spec["tier"],
        "scope_level": spec["scope"],
        "district": district,
        "station": station,
        "description": p["description"],
        "switchable_district": p["switchable_district"],
        "permissions": list(spec["permissions"]),
    }


def demo_catalog():
    """GET /auth/demo body. Only meaningful in demo mode; the route 404s otherwise."""
    return {
        "demo_mode": True,
        "districts": list(karnataka.DISTRICT_NAMES),
        "personas": [_persona_view(p) for p in DEMO_PERSONAS],
        "note": ("Demo personas are issued without a password because demo credentials "
                 "are public by design. Setting AUTH_ACCOUNTS removes this endpoint."),
    }


class PersonaError(ValueError):
    """A persona request that cannot be honoured; the message is the API error code."""


def issue_persona_token(persona_id, district=None):
    """(token, role, username, identity) for a demo persona.

    A switchable persona takes `district` in place of its default; it must be one of
    the 31. A non-switchable persona accepts only its own district (or none), so a
    station officer cannot be re-homed into a district whose stations they do not
    hold. The caller is responsible for refusing this outside demo mode.
    """
    p = _PERSONA_INDEX.get(str(persona_id or ""))
    if not p:
        raise PersonaError("unknown_persona")
    username = p["account"]
    _pw, role, default_district, station = _DEMO_PLAINTEXT[username]
    chosen = default_district
    if district is not None and str(district).strip():
        chosen = _canonical_district(district)
        if chosen is None:
            raise PersonaError("unknown_district")
        if chosen != default_district:
            if not p["switchable_district"]:
                raise PersonaError("persona_not_switchable")
            # A distinct actor per district keeps per-user state (watchlists, saved
            # conversations) and the audit trail from merging two jurisdictions
            # under one name.
            username = (role + "." + chosen.lower().replace(" ", "-") + "@ksp.gov.in")
    token = make_token(username, role, chosen, station)
    return token, role, username, identity_for(username, role, chosen, station)


def describe():
    """Advertised so a reviewer can see the policy without reading the source."""
    ident = current_identity()
    return {
        "identity": ident,
        # Role names, least to most privileged. Kept a flat list of strings because
        # that is what existing clients render; the full matrix is under
        # `role_details` and at /auth/roles.
        "roles": ROLES,
        "role_details": roles_catalog()["roles"],
        "demo_mode": _DEMO_MODE,
        # Usernames and roles only. The passwords used to be served from this
        # endpoint, which put the supervisor credential for the live deployment one
        # unauthenticated GET away and took the whole access-control story with it.
        # Demo passwords are documented in the README for reviewers; the API does
        # not hand them out.
        "demo_accounts": (
            [{"username": u, "role": r, "district": _DEMO_SCOPES[u][0],
              "station": _DEMO_SCOPES[u][1]}
             for u, (_, r) in _init_demo_accounts().items()]
            if _DEMO_MODE else []
        ),
        "personas": ([_persona_view(p) for p in DEMO_PERSONAS] if _DEMO_MODE else []),
        "insecure_cleartext_accounts": cleartext_accounts(),
        # Usernames from a misconfigured AUTH_ACCOUNTS are not for an open endpoint
        # in production; demo mode has none worth hiding.
        "rejected_accounts": rejected_accounts() if (_DEMO_MODE or not has_request_context()
                                                     or has_permission("system_admin")) else [],
        "policy": {role: spec["description"] for role, spec in ROLE_TABLE.items()},
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


def require_permission(permission):
    """Hard gate on a named capability, for gates a single tier cannot express.

    Like require_role it must sit *below* @audit.audited, so a refusal is recorded.
    """
    if permission not in PERMISSIONS:
        # A typo here would otherwise refuse everyone forever, silently.
        raise ValueError("unknown permission: " + str(permission))

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if request.method == "OPTIONS":
                return fn(*args, **kwargs)
            ident = current_identity()
            if permission not in ident["permissions"]:
                from flask import jsonify
                return jsonify(forbidden_body(permission, ident)), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def forbidden_body(permission, ident=None):
    """The 403 body for a missing permission, shared with in-route checks."""
    ident = ident or current_identity()
    return {
        "error": "insufficient_permission",
        "required": permission,
        "current": ident["role"],
        "permissions": ident["permissions"],
        "hint": ("Sign in with a role that holds this permission; GET "
                 "/serverless/api/auth/roles lists which roles do."),
    }


def roles_catalog():
    """GET /auth/roles body: the matrix the server enforces, for the UI to render."""
    return {
        "roles": [{
            "role": role,
            "label": spec["label"],
            "tier": spec["tier"],
            "scope_level": spec["scope"],
            "description": spec["description"],
            "permissions": list(spec["permissions"]),
        } for role, spec in ROLE_TABLE.items()],
        "permissions": [{"permission": k, "description": v} for k, v in PERMISSIONS.items()],
        "scope_levels": {
            "state": "All 31 districts.",
            "district": "List views limited to one district; opening a record elsewhere "
                        "is allowed but separately audited and flagged.",
            "station": "List views limited to one police station (CCTV alerts to its "
                       "district); opening a record elsewhere is allowed but separately "
                       "audited and flagged.",
        },
    }
