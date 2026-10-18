"""Roles, permissions and jurisdiction.

The three-role ladder (analyst < investigator < supervisor) could not say what a
police hierarchy needs it to say: that a Sub-Inspector and a DSP may both read case
files, but one a station's and the other a district's; or that an SHO may verify a
CCTV match while an SI may only acknowledge it. These tests pin the model that
replaced it, and — just as important — that the legacy roles kept every power they
had, because the rest of the suite and every existing client depends on them.

What matters most here is that jurisdiction is *enforced*, not displayed: a scoped
token narrows list views from its own signed claims, a query parameter cannot widen
it, a forged or malformed scope never verifies, and reading across the boundary
leaves its own audit entry.
"""
import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit  # noqa: E402
import auth  # noqa: E402
import karnataka  # noqa: E402
import main  # noqa: E402
import surveillance  # noqa: E402

API = "/serverless/api"

# The contract the sign-in page is built against. Pinned literally so a change to the
# role table is a deliberate, visible edit here too.
CONTRACT = {
    "analyst": (0, "state", "Analyst"),
    "si": (1, "station", "Sub-Inspector (IO)"),
    "sho": (1, "station", "Station House Officer"),
    "investigator": (1, "state", "Investigator"),
    "dsp": (1, "district", "Deputy SP"),
    "sp": (2, "district", "Superintendent of Police"),
    "supervisor": (2, "state", "Supervisor"),
    "dgp": (2, "state", "State DGP"),
    "admin": (3, "state", "Admin"),
}
REQUIRED_PERMISSIONS = {
    "view_aggregates", "view_cases", "view_dossiers", "view_network", "use_copilot",
    "surveillance_view", "surveillance_decide", "surveillance_escalate",
    "watchlist_names", "audit_read", "contest_resolve", "system_admin",
}

# persona id -> (role, district, station)
PERSONAS = {
    "dgp": ("dgp", None, None),
    "admin": ("admin", None, None),
    "analyst": ("analyst", None, None),
    "sp": ("sp", "Bengaluru Urban", None),
    "dsp": ("dsp", "Mysuru", None),
    "sho": ("sho", "Bengaluru Urban", "Koramangala PS"),
    "si": ("si", "Bengaluru Urban", "Koramangala PS"),
}

# username -> (password, role, district, station)
ACCOUNTS = {
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


@pytest.fixture(scope="module")
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        c.get(f"{API}/health")          # the corpus loads lazily; once per module
        yield c


def persona_token(client, persona, district=None):
    body = {"persona": persona}
    if district is not None:
        body["district"] = district
    r = client.post(f"{API}/auth/demo", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]


def hdr(token):
    return {"Authorization": "Bearer " + token}


def bearer(role, district=None, station=None, user=None):
    return hdr(auth.make_token(user or ("test-" + role), role, district, station))


def _decode(token):
    body, sig = token.rsplit(".", 1)
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))), sig


def _encode(payload):
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


# ------------------------------------------------------------------ the role table
def test_the_role_table_matches_the_contract():
    assert set(auth.ROLES) == set(CONTRACT)
    for role, (tier, level, label) in CONTRACT.items():
        spec = auth.ROLE_TABLE[role]
        assert (spec["tier"], spec["scope"], spec["label"]) == (tier, level, label), role
    assert REQUIRED_PERMISSIONS <= set(auth.PERMISSIONS)


def test_roles_stay_ordered_least_to_most_privileged():
    tiers = [auth.rank(r) for r in auth.ROLES]
    assert tiers == sorted(tiers)
    assert auth.ROLES[0] == "analyst" and auth.ROLES[-1] == "admin"


def test_tier_gates_admit_the_new_ranks():
    """Every existing require_role gate keeps working without an edit: si/sho/dsp
    pass an investigator gate, sp/dgp/admin a supervisor gate."""
    for role in ("si", "sho", "dsp", "investigator"):
        assert auth.rank(role) >= auth.rank("investigator") > auth.rank("analyst"), role
        assert auth.rank(role) < auth.rank("supervisor"), role
    for role in ("sp", "supervisor", "dgp", "admin"):
        assert auth.rank(role) >= auth.rank("supervisor"), role
    assert auth.rank("admin") > auth.rank("dgp")


def test_the_permission_matrix():
    """Spot-checks of the distinctions the hierarchy actually draws."""
    p = {r: set(auth.permissions_for(r)) for r in auth.ROLES}
    assert p["analyst"] == {"view_aggregates"}
    assert "surveillance_view" in p["si"] and "surveillance_decide" not in p["si"]
    assert "surveillance_decide" in p["sho"] and "surveillance_escalate" not in p["sho"]
    assert {"surveillance_decide", "surveillance_escalate"} <= p["dsp"]
    assert "watchlist_names" not in p["dsp"]
    assert "watchlist_names" in p["sp"]
    for privileged in ("audit_read", "contest_resolve", "system_admin"):
        assert privileged not in p["sp"], f"a district SP should not hold {privileged}"
    for role in ("supervisor", "dgp", "admin"):
        assert p[role] == set(auth.PERMISSIONS), role
    # Legacy investigator: everything it could do before, nothing it could not.
    assert p["investigator"] == {
        "view_aggregates", "view_cases", "view_dossiers", "view_network", "use_copilot",
        "surveillance_view", "surveillance_decide", "surveillance_escalate", "register_fir"}
    # FIR registration: every casework role, never the statistical desk.
    assert "register_fir" not in p["analyst"]
    assert all("register_fir" in p[r] for r in ("si", "sho", "dsp", "sp", "dgp", "admin"))


def test_an_unknown_permission_cannot_be_used_as_a_gate():
    with pytest.raises(ValueError):
        auth.require_permission("view_evrything")


def test_the_roles_endpoint_publishes_the_enforced_matrix(client):
    r = client.get(f"{API}/auth/roles")
    assert r.status_code == 200
    rows = {row["role"]: row for row in r.get_json()["roles"]}
    assert [row["role"] for row in r.get_json()["roles"]] == auth.ROLES
    for role, (tier, level, label) in CONTRACT.items():
        row = rows[role]
        assert (row["tier"], row["scope_level"], row["label"]) == (tier, level, label)
        assert row["permissions"] == auth.permissions_for(role)
        assert row["description"]


# ------------------------------------------------------------------ signing in
@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_every_persona_signs_in_with_the_right_identity(client, persona):
    role, district, station = PERSONAS[persona]
    r = client.post(f"{API}/auth/demo", json={"persona": persona})
    assert r.status_code == 200
    body = r.get_json()
    assert body["role"] == role and body["expires_in"] == auth.TOKEN_TTL_SECONDS
    ident = body["identity"]
    assert ident["scope"] == {"level": CONTRACT[role][1], "district": district,
                              "station": station}
    assert ident["permissions"] == auth.permissions_for(role)
    assert ident["role_label"] == CONTRACT[role][2] and ident["tier"] == CONTRACT[role][0]

    # And the token says the same thing to every later request.
    me = client.get(f"{API}/auth/me", headers=hdr(body["token"])).get_json()["identity"]
    assert me["authenticated"] is True and me["user"] == body["username"]
    assert me["role"] == role and me["scope"] == ident["scope"]
    assert me["permissions"] == ident["permissions"]


@pytest.mark.parametrize("username", sorted(ACCOUNTS))
def test_every_demo_account_logs_in_with_its_password(client, username):
    password, role, district, station = ACCOUNTS[username]
    r = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    body = r.get_json()
    assert body["role"] == role and body["username"] == username
    assert body["identity"]["scope"]["district"] == district
    assert body["identity"]["scope"]["station"] == station
    payload = auth.verify_token(body["token"])
    assert payload["r"] == role and payload.get("d") == district and payload.get("st") == station


def test_legacy_logins_are_unchanged(client):
    for username, role in (("analyst", "analyst"), ("io", "investigator"), ("sp", "supervisor")):
        body = client.post(f"{API}/auth/login", json={
            "username": username, "password": ACCOUNTS[username][0]}).get_json()
        assert body["role"] == role
        assert body["identity"]["scope"] == {"level": "state", "district": None, "station": None}


def test_anonymous_identity_is_an_analyst_with_aggregates_only(client):
    ident = client.get(f"{API}/auth/me").get_json()["identity"]
    assert ident["authenticated"] is False and ident["role"] == "analyst"
    assert ident["tier"] == 0 and ident["permissions"] == ["view_aggregates"]
    assert ident["scope"]["level"] == "state"


def test_auth_me_lists_the_new_roles_and_personas(client):
    me = client.get(f"{API}/auth/me").get_json()
    assert me["roles"] == auth.ROLES
    assert set(me["policy"]) == set(auth.ROLES)
    assert {p["id"] for p in me["personas"]} == set(PERSONAS)
    assert {a["username"] for a in me["demo_accounts"]} == set(ACCOUNTS)


def test_no_demo_password_is_served(client):
    bodies = [client.get(f"{API}/{path}").get_data(as_text=True)
              for path in ("auth/me", "auth/demo", "auth/roles")]
    for password in {pw for pw, *_ in ACCOUNTS.values()}:
        for body in bodies:
            assert password not in body, f"{password} served by the API"


# ------------------------------------------------------------------ demo endpoint
def test_the_demo_catalog_lists_personas_and_all_31_districts(client):
    r = client.get(f"{API}/auth/demo")
    assert r.status_code == 200
    body = r.get_json()
    assert body["demo_mode"] is True
    assert body["districts"] == karnataka.DISTRICT_NAMES and len(body["districts"]) == 31
    personas = {p["id"]: p for p in body["personas"]}
    assert set(personas) == set(PERSONAS)
    for pid, (role, district, station) in PERSONAS.items():
        p = personas[pid]
        for field in ("label", "role_label", "scope_level", "description", "switchable_district"):
            assert field in p, (pid, field)
        assert (p["role"], p["district"], p["station"]) == (role, district, station)
    assert personas["sp"]["switchable_district"] and personas["dsp"]["switchable_district"]
    assert not personas["sho"]["switchable_district"] and not personas["si"]["switchable_district"]


def test_the_demo_endpoint_does_not_exist_outside_demo_mode(client, monkeypatch):
    monkeypatch.setattr(auth, "_DEMO_MODE", False)
    assert client.get(f"{API}/auth/demo").status_code == 404
    r = client.post(f"{API}/auth/demo", json={"persona": "dgp"})
    assert r.status_code == 404
    assert "token" not in (r.get_json() or {})


def test_a_switchable_persona_takes_any_real_district(client):
    r = client.post(f"{API}/auth/demo", json={"persona": "sp", "district": "kalaburagi"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["identity"]["scope"] == {"level": "district", "district": "Kalaburagi",
                                         "station": None}
    # A distinct actor, so two districts' work never merges under one name.
    assert body["username"] != "sp.bengaluru@ksp.gov.in"
    assert auth.verify_token(body["token"])["d"] == "Kalaburagi"


@pytest.mark.parametrize("district", ["Mangaluru", "Hubballi-Dharwad", "Atlantis", "   "])
def test_a_switchable_persona_rejects_a_non_district(client, district):
    """Mangaluru and Hubballi-Dharwad are commissionerates, not districts."""
    r = client.post(f"{API}/auth/demo", json={"persona": "dsp", "district": district})
    if district.strip():
        assert r.status_code == 400 and r.get_json()["error"] == "unknown_district"
        assert "token" not in r.get_json()
    else:
        # Blank means "no choice": the persona's own district.
        assert r.status_code == 200
        assert r.get_json()["identity"]["scope"]["district"] == "Mysuru"


def test_a_station_persona_cannot_be_rehomed(client):
    r = client.post(f"{API}/auth/demo", json={"persona": "sho", "district": "Mysuru"})
    assert r.status_code == 400 and r.get_json()["error"] == "persona_not_switchable"
    r = client.post(f"{API}/auth/demo", json={"persona": "dgp", "district": "Mysuru"})
    assert r.status_code == 400
    r = client.post(f"{API}/auth/demo", json={"persona": "root"})
    assert r.status_code == 400 and r.get_json()["error"] == "unknown_persona"


def test_persona_issue_is_audited(client):
    client.post(f"{API}/auth/demo", json={"persona": "dsp", "district": "Udupi"})
    entry = audit.recent(limit=5)[0]
    assert entry["action"] == "auth_demo_persona" and entry["result"] == "ok"
    assert entry["granted_role"] == "dsp"
    assert entry["granted_scope"]["district"] == "Udupi"
    client.post(f"{API}/auth/demo", json={"persona": "dsp", "district": "Atlantis"})
    entry = audit.recent(limit=5)[0]
    assert entry["action"] == "auth_demo_persona" and entry["result"] == "denied"


# ------------------------------------------------------------------ tokens
def test_a_forged_role_is_rejected(client):
    token = persona_token(client, "si")
    payload, sig = _decode(token)
    payload["r"] = "dgp"
    payload.pop("d", None)
    payload.pop("st", None)
    forged = _encode(payload) + "." + sig
    assert auth.verify_token(forged) is None
    assert client.get(f"{API}/cases", headers=hdr(forged)).status_code == 403


def test_a_widened_scope_is_rejected(client):
    """Editing the signed district or station breaks the signature, so a station
    officer cannot re-sign themselves a district or the whole state."""
    payload, sig = _decode(persona_token(client, "sho"))
    for edit in ({"st": "Whitefield PS"}, {"d": "Mysuru"}, {"r": "dsp"}):
        forged = dict(payload, **edit)
        assert auth.verify_token(_encode(forged) + "." + sig) is None, edit
    stripped = {k: v for k, v in payload.items() if k not in ("d", "st")}
    assert auth.verify_token(_encode(stripped) + "." + sig) is None


def test_a_malformed_scope_never_verifies_even_when_signed():
    """A correctly signed token whose scope does not fit its role is refused, not
    served unfiltered — "no district" must never mean "every district"."""
    bad = [
        ("sho", "Bengaluru Urban", None),         # station role without a station
        ("si", None, "Koramangala PS"),           # station without its district
        ("si", "Mysuru", "Koramangala PS"),       # station not on that district's roster
        ("dsp", None, None),                      # district role without a district
        ("sp", "Mangaluru", None),                # a commissionerate, not a district
        ("dsp", "Mysuru", "Lashkar PS"),          # district role carrying a station
        ("dgp", "Mysuru", None),                  # state role carrying a district
        ("supervisor", "Bengaluru Urban", None),
    ]
    for role, d, st in bad:
        assert auth.verify_token(auth.make_token("x", role, d, st)) is None, (role, d, st)
    assert auth.verify_token(auth.make_token("x", "sho", "Bengaluru Urban", "Koramangala PS"))


def test_the_original_three_role_tokens_still_verify():
    for role in ("analyst", "investigator", "supervisor"):
        payload = auth.verify_token(auth.make_token("legacy", role))
        assert payload["r"] == role and "d" not in payload and "st" not in payload


# ------------------------------------------------------------------ case list scope
def test_a_station_role_lists_only_its_station(client):
    r = client.get(f"{API}/cases?limit=400", headers=hdr(persona_token(client, "si")))
    assert r.status_code == 200
    body = r.get_json()
    firs = main.data_cache["firs"]
    expected = int(((firs["district"] == "Bengaluru Urban")
                    & (firs["police_station"] == "Koramangala PS")).sum())
    assert expected > 0 and body["total"] == expected
    assert body["cases"], "no rows returned"
    assert {c["police_station"] for c in body["cases"]} == {"Koramangala PS"}
    assert {c["district"] for c in body["cases"]} == {"Bengaluru Urban"}
    assert body["scope"]["restricted"] is True
    assert body["scope"]["station"] == "Koramangala PS"
    assert body["districts"] == ["Bengaluru Urban"]


def test_a_district_role_lists_only_its_district(client):
    r = client.get(f"{API}/cases?limit=400", headers=hdr(persona_token(client, "dsp")))
    body = r.get_json()
    firs = main.data_cache["firs"]
    assert body["total"] == int((firs["district"] == "Mysuru").sum())
    assert {c["district"] for c in body["cases"]} == {"Mysuru"}
    assert body["scope"] == dict(body["scope"], level="district", district="Mysuru",
                                 restricted=True)


def test_query_parameters_cannot_widen_the_scope(client):
    token = persona_token(client, "sho")
    r = client.get(f"{API}/cases?district=Mysuru&limit=400", headers=hdr(token))
    body = r.get_json()
    assert r.status_code == 200 and body["total"] == 0 and body["cases"] == []
    assert body["scope"]["requested_district_outside_scope"] == "Mysuru"
    # Narrowing inside the scope still works.
    r = client.get(f"{API}/cases?district=Bengaluru%20Urban&limit=400", headers=hdr(token))
    assert {c["police_station"] for c in r.get_json()["cases"]} == {"Koramangala PS"}
    # Parameters that look like scope are not scope.
    r = client.get(f"{API}/cases?station=Whitefield%20PS&police_station=Whitefield%20PS"
                   "&scope=state&level=state", headers=hdr(token))
    assert {c["police_station"] for c in r.get_json()["cases"]} == {"Koramangala PS"}


def test_legacy_investigator_and_supervisor_still_list_the_whole_state(client):
    firs = main.data_cache["firs"]
    for role in ("investigator", "supervisor", "dgp", "admin"):
        body = client.get(f"{API}/cases", headers=bearer(role)).get_json()
        assert body["total"] == len(firs), role
        assert body["scope"]["restricted"] is False and body["scope"]["level"] == "state"
        assert len(body["districts"]) == firs["district"].nunique()


def test_an_analyst_still_cannot_list_cases(client):
    for headers in ({}, hdr(persona_token(client, "analyst"))):
        r = client.get(f"{API}/cases", headers=headers)
        assert r.status_code == 403
        assert r.get_json()["required"] == "view_cases"


# ------------------------------------------------------------------ single records
@pytest.fixture(scope="module")
def cases(client):
    firs = main.data_cache["firs"]
    home = firs[(firs["district"] == "Bengaluru Urban") & (firs["police_station"] == "Koramangala PS")]
    away = firs[firs["district"] == "Mysuru"]
    return str(home.iloc[0]["case_id"]), str(away.iloc[0]["case_id"])


def _entries_since(before):
    rows = audit.recent(limit=2000)
    return rows[:max(0, len(rows) - before)]


def test_an_out_of_jurisdiction_case_read_is_allowed_flagged_and_audited(client, cases):
    _home, away = cases
    token = persona_token(client, "si")
    before = len(audit.recent(limit=2000))
    r = client.get(f"{API}/case/{away}", headers={**hdr(token), "X-Access-Reason": "cross-district MO"})
    assert r.status_code == 200
    j = r.get_json()["jurisdiction"]
    assert j["in_scope"] is False and j["audited"] is True
    assert j["record"]["district"] == "Mysuru"
    assert j["scope"]["station"] == "Koramangala PS"

    new = _entries_since(before)
    actions = [e["action"] for e in new]
    # The dossier read is still the newest entry; the jurisdiction entry sits beside it.
    assert actions[0] == "read_case_dossier"
    flagged = [e for e in new if e["action"] == "out_of_jurisdiction_read"]
    assert len(flagged) == 1
    assert flagged[0]["subject_id"] == away and flagged[0]["actor"] == "si.koramangala@ksp.gov.in"
    assert flagged[0]["scope_station"] == "Koramangala PS"
    assert flagged[0]["reason"] == "cross-district MO"


def test_an_in_jurisdiction_case_read_leaves_no_extra_entry(client, cases):
    home, _away = cases
    before = len(audit.recent(limit=2000))
    r = client.get(f"{API}/case/{home}", headers=hdr(persona_token(client, "si")))
    assert r.status_code == 200 and r.get_json()["jurisdiction"]["in_scope"] is True
    assert "out_of_jurisdiction_read" not in [e["action"] for e in _entries_since(before)]


def test_a_statewide_role_is_never_flagged(client, cases):
    _home, away = cases
    before = len(audit.recent(limit=2000))
    r = client.get(f"{API}/case/{away}", headers=bearer("investigator"))
    assert r.get_json()["jurisdiction"]["in_scope"] is True
    assert "out_of_jurisdiction_read" not in [e["action"] for e in _entries_since(before)]


def test_a_missing_case_is_still_a_plain_404(client):
    before = len(audit.recent(limit=2000))
    r = client.get(f"{API}/case/NOT-A-REAL-CASE", headers=hdr(persona_token(client, "si")))
    assert r.status_code == 404
    assert "out_of_jurisdiction_read" not in [e["action"] for e in _entries_since(before)]


def _suspects_by_reach():
    """(one person with a Koramangala case, one with no Bengaluru Urban case)."""
    inside = outside = None
    for sid in main.data_cache["suspects"]["suspect_id"].astype(str):
        rows = main._linked_case_rows(sid)
        if rows.empty:
            continue
        if inside is None and (rows["police_station"] == "Koramangala PS").any():
            inside = sid
        if outside is None and not (rows["district"] == "Bengaluru Urban").any():
            outside = sid
        if inside and outside:
            break
    return inside, outside


def test_a_suspect_is_in_scope_if_any_linked_case_is(client):
    inside, outside = _suspects_by_reach()
    if not (inside and outside):
        pytest.skip("corpus has no suitable pair of offenders")
    token = persona_token(client, "si")

    r = client.get(f"{API}/suspect/{inside}", headers=hdr(token))
    assert r.status_code == 200
    assert r.get_json()["jurisdiction"]["in_scope"] is True
    assert r.get_json()["jurisdiction"]["record"]["linked_cases_in_scope"] >= 1

    before = len(audit.recent(limit=2000))
    r = client.get(f"{API}/suspect/{outside}", headers=hdr(token))
    assert r.status_code == 200
    j = r.get_json()["jurisdiction"]
    assert j["in_scope"] is False and j["record"]["linked_cases_in_scope"] == 0
    flagged = [e for e in _entries_since(before) if e["action"] == "out_of_jurisdiction_read"]
    assert flagged and flagged[0]["subject_type"] == "offender"
    assert flagged[0]["subject_id"] == outside


# ------------------------------------------------------------------ surveillance
def _bengaluru_sighting(client, token):
    """A recent past sighting from a Bengaluru Urban camera with a match on it."""
    feed = client.get(f"{API}/surveillance/feed?matches_only=1&limit=300",
                      headers=hdr(token)).get_json()
    for e in feed.get("events", []):
        if e["district"] == "Bengaluru Urban":
            return e["event_id"]
    return None


def _other_district_sighting():
    import time as _t
    surveillance.ensure(main.data_cache)
    now = _t.time()
    st = surveillance._state
    for e in surveillance._events_between(now - 3600, now):
        if st["cameras"][st["cam_index"][e["camera_id"]]]["district"] != "Bengaluru Urban":
            return e["event_id"]
    return None


def test_scoped_feed_and_alerts_show_only_the_scope_district(client):
    token = persona_token(client, "sho")
    for path in ("surveillance/feed?limit=300", "surveillance/alerts?limit=300"):
        body = client.get(f"{API}/{path}", headers=hdr(token)).get_json()
        rows = body.get("events", body.get("alerts", []))
        assert {e["district"] for e in rows} <= {"Bengaluru Urban"}, path
        assert body["scope"]["restricted"] is True and body["scope"]["district"] == "Bengaluru Urban"


def test_surveillance_district_cannot_be_widened(client):
    token = persona_token(client, "sho")
    feed = client.get(f"{API}/surveillance/feed?district=Mysuru&limit=300", headers=hdr(token)).get_json()
    assert feed["events"] == [] and feed["counts"]["detections"] == 0
    assert feed["scope"]["requested_district_outside_scope"] == "Mysuru"
    alerts = client.get(f"{API}/surveillance/alerts?district=Mysuru", headers=hdr(token)).get_json()
    assert alerts["alerts"] == [] and alerts["counts"]["P1"] == 0


def test_a_statewide_role_sees_every_camera_district(client):
    body = client.get(f"{API}/surveillance/feed?limit=300", headers=bearer("investigator")).get_json()
    assert body["scope"]["restricted"] is False
    assert len({e["district"] for e in body["events"]}) > 1


def test_an_si_cannot_verify_a_match_but_an_sho_can(client):
    si = persona_token(client, "si")
    sho = persona_token(client, "sho")
    event_id = _bengaluru_sighting(client, sho)
    if not event_id:
        pytest.skip("no Bengaluru Urban match in the current window")
    url = f"{API}/surveillance/alerts/{event_id}"

    r = client.post(url, json={"action": "verify", "note": "compared against dossier photo"},
                    headers=hdr(si))
    assert r.status_code == 403
    assert r.get_json()["error"] == "insufficient_permission"
    assert r.get_json()["required"] == "surveillance_decide"
    denial = audit.recent(limit=3)[0]
    assert denial["action"] == "decide_surveillance_alert" and denial["result"] == "denied"
    assert denial["status"] == 403

    # Acknowledging only says "seen", and any tier-1 role may do it.
    r = client.post(url, json={"action": "acknowledge"}, headers=hdr(si))
    assert r.status_code == 200 and r.get_json()["alert"]["status"] == "acknowledged"

    r = client.post(url, json={"action": "verify", "note": "compared against dossier photo"},
                    headers=hdr(sho))
    assert r.status_code == 200 and r.get_json()["alert"]["status"] == "verified"

    # The SHO may verify but not escalate; the DSP may escalate.
    r = client.post(url, json={"action": "escalate"}, headers=hdr(sho))
    assert r.status_code == 403 and r.get_json()["required"] == "surveillance_escalate"
    dsp_blr = persona_token(client, "dsp", "Bengaluru Urban")
    r = client.post(url, json={"action": "escalate"}, headers=hdr(dsp_blr))
    assert r.status_code == 200 and r.get_json()["alert"]["status"] == "escalated"


def test_a_scoped_officer_cannot_decide_another_districts_alert(client):
    event_id = _other_district_sighting()
    if not event_id:
        pytest.skip("no sighting outside Bengaluru Urban in the window")
    r = client.post(f"{API}/surveillance/alerts/{event_id}",
                    json={"action": "acknowledge"}, headers=hdr(persona_token(client, "sho")))
    assert r.status_code == 403 and r.get_json()["error"] == "outside_jurisdiction"
    # A statewide role still can, exactly as before.
    r = client.post(f"{API}/surveillance/alerts/{event_id}",
                    json={"action": "acknowledge"}, headers=bearer("investigator"))
    assert r.status_code == 200


def test_legacy_investigator_keeps_every_decision(client):
    event_id = _other_district_sighting()
    if not event_id:
        pytest.skip("no sighting in the window")
    url = f"{API}/surveillance/alerts/{event_id}"
    for action, note in (("verify", "checked"), ("reopen", None), ("reject", "not him"),
                         ("escalate", None)):
        body = {"action": action}
        if note:
            body["note"] = note
        assert client.post(url, json=body, headers=bearer("investigator")).status_code == 200, action


def test_an_unknown_decision_is_still_a_400(client):
    event_id = _other_district_sighting()
    if not event_id:
        pytest.skip("no sighting in the window")
    for action in ("delete", ["verify"], {"verify": True}, None):
        r = client.post(f"{API}/surveillance/alerts/{event_id}", json={"action": action},
                        headers=hdr(persona_token(client, "si")))
        assert r.status_code == 400, action


# ------------------------------------------------------------------ privileged routes
ADMIN_POSTS = [f"{API}/settings", f"{API}/retention/purge"]
AUDIT_GETS = [f"{API}/audit-log", f"{API}/audit-log/verify", f"{API}/subject-report/SUS-1001"]
CONTEST_GETS = [f"{API}/contests"]


def test_admin_and_dgp_can_reach_settings_and_retention(client):
    for persona in ("admin", "dgp"):
        token = persona_token(client, persona)
        for route in ADMIN_POSTS:
            assert client.post(route, json={}, headers=hdr(token)).status_code == 200, (persona, route)
        assert client.get(f"{API}/retention", headers=hdr(token)).status_code == 200


def test_legacy_supervisor_keeps_every_power(client):
    h = bearer("supervisor")
    for route in ADMIN_POSTS:
        assert client.post(route, json={}, headers=h).status_code == 200, route
    for route in AUDIT_GETS + CONTEST_GETS + [f"{API}/retention"]:
        assert client.get(route, headers=h).status_code in (200, 409), route
    rows = client.get(f"{API}/recidivism", headers=h).get_json()["top"]
    assert rows and not any(r.get("redacted") for r in rows)


@pytest.mark.parametrize("persona", ["sp", "dsp", "sho", "si", "analyst"])
def test_district_and_station_ranks_cannot_reach_administration(client, persona):
    """A district SP passes a tier-2 check, which is exactly why these gates are
    permissions now: an SP is not a state administrator."""
    token = persona_token(client, persona)
    for route in ADMIN_POSTS:
        r = client.post(route, json={}, headers=hdr(token))
        assert r.status_code == 403 and r.get_json()["required"] == "system_admin", (persona, route)
    for route in AUDIT_GETS + CONTEST_GETS:
        assert client.get(route, headers=hdr(token)).status_code == 403, (persona, route)


def test_legacy_investigator_still_cannot_reach_administration(client):
    h = bearer("investigator")
    for route in ADMIN_POSTS:
        assert client.post(route, json={}, headers=h).status_code == 403
    for route in AUDIT_GETS + CONTEST_GETS:
        assert client.get(route, headers=h).status_code == 403


@pytest.mark.parametrize("persona", ["si", "sho", "dsp", "sp"])
def test_the_new_tier_one_ranks_pass_investigator_gates(client, persona):
    token = persona_token(client, persona)
    for route in ("network", "anomalies", "registry/criminals", "officers"):
        assert client.get(f"{API}/{route}", headers=hdr(token)).status_code == 200, (persona, route)


# ------------------------------------------------------------------ the watchlist
def test_a_district_sp_sees_names_only_for_their_district(client):
    body = client.get(f"{API}/recidivism", headers=hdr(persona_token(client, "sp", "Kodagu"))).get_json()
    ident = auth.identity_for("t", "sp", "Kodagu")
    for row in body["top"]:
        in_scope = main._subject_in_jurisdiction(
            row["criminal_id"], ident) if not row.get("redacted") else None
        if row.get("redacted"):
            assert str(row["criminal_id"]).startswith("PSN-") and "probability" not in row
        else:
            assert in_scope is True and "name" in row
    assert body["access"]["required_permission"] == "watchlist_names"
    assert body["access"]["redacted_rows"] == sum(1 for r in body["top"] if r.get("redacted"))


def test_a_dsp_and_an_si_see_only_the_redacted_watchlist(client):
    for persona in ("dsp", "si"):
        body = client.get(f"{API}/recidivism", headers=hdr(persona_token(client, persona))).get_json()
        assert body["access"]["redacted"] is True
        assert all(r.get("redacted") for r in body["top"]), persona


def test_the_dgp_sees_the_whole_named_watchlist(client):
    body = client.get(f"{API}/analytics", headers=hdr(persona_token(client, "dgp"))).get_json()
    rows = body["recidivism"]["top"]
    if rows:
        assert body["recidivism"]["redacted"] is False
        assert not any(r.get("redacted") for r in rows)


# ------------------------------------------------------------------ AUTH_ACCOUNTS
def test_configured_accounts_carry_a_validated_jurisdiction(monkeypatch):
    h = auth.hash_password("pw-test")
    monkeypatch.setenv("AUTH_ACCOUNTS", ",".join([
        f"chief:{h}:dgp",
        f"sho-hebbal:{h}:sho:Bengaluru Urban:Hebbal PS",
        f"sp-udupi:{h}:sp:Udupi",
        f"bad-station:{h}:sho:Mysuru:Hebbal PS",
        f"bad-district:{h}:dsp:Mangaluru",
        f"no-scope:{h}:si",
        f"bad-role:{h}:root",
    ]))
    accounts = auth._accounts()
    assert set(accounts) == {"chief", "sho-hebbal", "sp-udupi"}
    assert set(auth.rejected_accounts()) == {"bad-station", "bad-district", "no-scope", "bad-role"}

    token, role = auth.authenticate("sho-hebbal", "pw-test")
    payload = auth.verify_token(token)
    assert role == "sho" and payload["d"] == "Bengaluru Urban" and payload["st"] == "Hebbal PS"
    token, role = auth.authenticate("sp-udupi", "pw-test")
    assert auth.verify_token(token)["d"] == "Udupi" and "st" not in auth.verify_token(token)
    token, _ = auth.authenticate("chief", "pw-test")
    assert "d" not in auth.verify_token(token)
    # A rejected entry cannot sign in at all, rather than signing in unscoped.
    token, reason = auth.authenticate("no-scope", "pw-test")
    assert token is None and reason == "invalid_credentials"
    auth._clear_failures("no-scope")
