"""HTTP-level tests for the Flask API.

Before this file the application had 74 tests and not one of them made a request.
Every route, every access decision and every error path was unverified, which is
how `auth.require_role` came to be written, unit-tested, and applied to nothing.

The tests here are deliberately about *enforcement* rather than plumbing. The two
that matter most are:

* `test_no_offender_name_leaks_to_anonymous_callers`, which sweeps every route with
  no credentials and asserts that no real offender name from the registry appears
  in any response body. That is a property of the whole surface, so it keeps
  holding as routes are added — unlike a per-route assertion, which only covers the
  routes someone remembered to list.
* `test_analytics_does_not_bypass_the_recidivism_gate`, which pins the specific
  failure that made the access control decorative: `/analytics` served the same
  watchlist `/recidivism` gates, one route over, with no credentials.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit  # noqa: E402
import auth  # noqa: E402
import privacy  # noqa: E402
from main import app, data_cache  # noqa: E402

API = "/serverless/api"

# Routes that can return a person: a name, a dossier, a registry row, a graph node.
PERSON_LEVEL = [
    f"{API}/network",
    f"{API}/cases",
    f"{API}/anomalies",
    f"{API}/registry/criminals",
    f"{API}/cyber/rings",
    f"{API}/predicted-links",
    f"{API}/syndicates",
    f"{API}/officers",
]
# Routes that describe the state of policing, not the people in it.
AGGREGATE = [
    f"{API}/health",
    f"{API}/stats",
    f"{API}/heatmap",
    f"{API}/forecast",
    f"{API}/clusters",
    f"{API}/socioeconomic",
    f"{API}/lifecycle",
    f"{API}/operational-index",
    f"{API}/pendency",
    f"{API}/models",
    f"{API}/dispatch",
    f"{API}/auth/me",
    # Mixed: aggregate, but carries person-level blocks that must arrive redacted.
    # Its absence from this list is why two leaks here went unnoticed.
    f"{API}/analytics",
    f"{API}/recidivism",
]
ADMIN = [f"{API}/sync", f"{API}/settings"]


def bearer(role):
    return {"Authorization": "Bearer " + auth.make_token("test-" + role, role)}


@pytest.fixture(scope="module")
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        # The corpus loads lazily on first request; do it once for the module.
        c.get(f"{API}/health")
        yield c


@pytest.fixture(scope="module")
def personal_identifiers(client):
    """Every shape of personal identifier the corpus actually contains.

    Names alone are not enough: a phone number, a UPI handle or a bank account is
    just as identifying, and the first version of this fixture missed a live leak of
    exactly those because it only knew about names.
    """
    out = []

    offenders = data_cache.get("criminals")
    if offenders is not None and not offenders.empty and "name" in offenders.columns:
        out += [str(n) for n in offenders["name"].dropna().unique()[:300]]

    officers = data_cache.get("officers")
    if officers is not None and not officers.empty and "name" in officers.columns:
        out += [str(n) for n in officers["name"].dropna().unique()[:100]]

    cyber = data_cache.get("cybercrime")
    if cyber is not None and not cyber.empty:
        for col in ("phone_numbers", "upi_ids", "bank_accounts"):
            if col in cyber.columns:
                for cell in cyber[col].dropna().head(120):
                    out += [t.strip() for t in str(cell).split(";") if len(t.strip()) > 6]

    values = sorted({v for v in out if len(v) > 6})
    if not values:
        pytest.skip("no identifiers available to test against")
    return values


# ------------------------------------------------------------------ access control
@pytest.mark.parametrize("route", PERSON_LEVEL)
def test_person_level_routes_refuse_anonymous(client, route):
    assert client.get(route).status_code == 403, f"{route} served a person to anonymous"


@pytest.mark.parametrize("route", PERSON_LEVEL)
def test_person_level_routes_admit_an_investigator(client, route):
    r = client.get(route, headers=bearer("investigator"))
    assert r.status_code == 200, f"{route} refused a valid investigator"


def test_case_and_suspect_dossiers_refuse_anonymous(client):
    assert client.get(f"{API}/case/anything").status_code == 403
    assert client.get(f"{API}/suspect/SUS-1001").status_code == 403


@pytest.mark.parametrize("route", AGGREGATE)
def test_aggregate_routes_stay_open(client, route):
    """Aggregate surfaces are deliberately reachable without credentials — the
    platform has to be legible to an evaluator who has none."""
    assert client.get(route).status_code == 200, f"{route} should not require a role"


@pytest.mark.parametrize("route", ADMIN)
def test_administrative_routes_require_a_supervisor(client, route):
    """Both of these were anonymous. /settings rewrote the process LLM key and
    /sync triggered a ~20s rebuild that doubles as a denial of service."""
    assert client.post(route, json={}).status_code == 403
    assert client.post(route, json={}, headers=bearer("investigator")).status_code == 403


def test_preflight_is_never_gated(client):
    """A CORS preflight carries no Authorization header. Gating it would 403 every
    browser POST before the real request is sent."""
    assert client.options(f"{API}/settings").status_code in (200, 204)


# ------------------------------------------------------------------ the bypasses
def test_analytics_does_not_bypass_the_recidivism_gate(client):
    payload = client.get(f"{API}/analytics").get_json()
    rows = (payload.get("recidivism") or {}).get("top") or []
    assert payload["recidivism"]["redacted"] is True
    for row in rows:
        assert row.get("redacted") is True
        assert str(row.get("criminal_id", "")).startswith("PSN-")
        assert "probability" not in row and "reoffence_pct" not in row


def test_stats_does_not_leak_the_highest_risk_named_offender(client):
    top = ((client.get(f"{API}/stats").get_json() or {})
           .get("models", {}).get("recidivism_top") or {})
    if top:
        assert top.get("redacted") is True
        assert str(top.get("criminal_id", "")).startswith("PSN-")


def test_supervisor_sees_the_unredacted_watchlist(client):
    payload = client.get(f"{API}/analytics", headers=bearer("supervisor")).get_json()
    rows = (payload.get("recidivism") or {}).get("top") or []
    if rows:
        assert payload["recidivism"]["redacted"] is False
        assert not str(rows[0].get("criminal_id", "")).startswith("PSN-")


def test_no_personal_identifier_leaks_to_anonymous_callers(client, personal_identifiers):
    """Sweep every route with no credentials; no real identifier may appear.

    Offender names, officer names, phone numbers, UPI handles, bank accounts. A
    property of the whole surface, so it keeps holding for routes added later — but
    only if the identifier set covers every shape the corpus holds. It previously
    covered only offender names and missed cyber ring identifiers on /stats.
    """
    leaks = []
    for route in AGGREGATE + PERSON_LEVEL:
        response = client.get(route)
        if response.status_code != 200:
            continue
        body = response.get_data(as_text=True)
        for value in personal_identifiers:
            if value in body:
                leaks.append(f"{route} leaked {value!r}")
                break
    assert not leaks, ("personal identifiers reachable without credentials: "
                       + "; ".join(leaks))


def test_analytics_does_not_leak_predicted_alliance_names(client):
    """/analytics is open and carried a_name / b_name from the suspects registry,
    duplicating what /predicted-links was gated to protect."""
    links = (client.get(f"{API}/analytics").get_json() or {}).get("predicted_links") or []
    for link in links:
        assert link.get("redacted") is True
        assert str(link.get("a_name", "")).startswith("PSN-")
        assert str(link.get("b_name", "")).startswith("PSN-")


def test_stats_does_not_leak_cyber_ring_identifiers(client):
    """Ring identifiers ARE the personal data: PHONE:<number>, UPI:<handle>,
    ACCT:<account>. /cyber/rings is gated; /stats served the same rows."""
    ext = (client.get(f"{API}/stats").get_json() or {}).get("extended") or {}
    for ring in ext.get("cyber_rings") or []:
        ident = str(ring.get("identifier", ""))
        assert ident.startswith("PSN-"), f"raw cyber identifier served: {ident}"
        assert "cases" not in ring, "linked case ids served with a redacted ring"


def test_an_investigator_still_sees_the_real_values(client):
    """Redaction must not apply above the gate, or the feature is broken rather
    than protected."""
    ext = (client.get(f"{API}/stats", headers=bearer("investigator")).get_json()
           or {}).get("extended") or {}
    rings = ext.get("cyber_rings") or []
    if rings:
        assert not str(rings[0].get("identifier", "")).startswith("PSN-")


# ------------------------------------------------------------------ credentials
def test_passwords_are_never_served_by_the_api(client):
    body = client.get(f"{API}/auth/me").get_data(as_text=True)
    for secret in ("scrb-analyst", "scrb-io", "scrb-sp"):
        assert secret not in body, "/auth/me is handing out credentials"


def test_demo_passwords_are_stored_hashed():
    for _user, (stored, _role) in auth._init_demo_accounts().items():
        assert stored.startswith("scrypt$"), "a cleartext password is held in memory"


def test_login_succeeds_and_issues_a_usable_token(client):
    r = client.post(f"{API}/auth/login", json={"username": "sp", "password": "scrb-sp"})
    assert r.status_code == 200
    token = r.get_json()["token"]
    assert auth.verify_token(token)["r"] == "supervisor"


def test_login_rejects_a_wrong_password(client):
    r = client.post(f"{API}/auth/login",
                    json={"username": "sp", "password": "not-the-password"})
    assert r.status_code == 401


def test_repeated_failures_are_locked_out(client):
    """An unthrottled login over a three-account credential space is brute-forceable
    in seconds."""
    user = "lockout-probe"
    codes = [client.post(f"{API}/auth/login",
                         json={"username": user, "password": "wrong"}).status_code
             for _ in range(auth._MAX_ATTEMPTS + 1)]
    assert 429 in codes, f"no lockout after {len(codes)} failures: {codes}"
    auth._clear_failures(user)


# ------------------------------------------------------------------ audit trail
def test_person_level_access_is_recorded(client):
    before = len(audit.recent(limit=2000))
    client.get(f"{API}/suspect/SUS-1001", headers=bearer("investigator"))
    entries = audit.recent(limit=2000)
    assert len(entries) > before, "a dossier was read without leaving a record"
    latest = entries[0]
    assert latest["action"] == "read_offender_dossier"
    assert latest["subject_id"] == "SUS-1001"
    assert latest["actor"] == "test-investigator"


def test_audit_records_a_stated_reason(client):
    client.get(f"{API}/suspect/SUS-1002",
               headers={**bearer("investigator"), "X-Access-Reason": "FIR-42 follow-up"})
    latest = audit.recent(limit=5)[0]
    assert latest["reason"] == "FIR-42 follow-up"
    assert latest["reason_given"] is True


def test_access_without_a_reason_is_visibly_unjustified(client):
    client.get(f"{API}/suspect/SUS-1003", headers=bearer("investigator"))
    assert audit.recent(limit=5)[0]["reason_given"] is False


def test_denied_access_is_also_recorded(client):
    client.post(f"{API}/auth/login", json={"username": "sp", "password": "wrong"})
    denials = [e for e in audit.recent(limit=50)
               if e["action"] == "auth_login" and e["result"] == "denied"]
    assert denials, "a failed login left no trace"


def test_audit_log_is_supervisor_only_and_self_recording(client):
    assert client.get(f"{API}/audit-log").status_code == 403
    assert client.get(f"{API}/audit-log", headers=bearer("investigator")).status_code == 403
    r = client.get(f"{API}/audit-log", headers=bearer("supervisor"))
    assert r.status_code == 200
    payload = r.get_json()
    assert "entries" in payload and "summary" in payload
    # Reading the log must itself be logged.
    assert any(e["action"] == "read_audit_log" for e in audit.recent(limit=20))


def test_audit_summary_reports_its_own_integrity_limits(client):
    """The note used to say the log was "not tamper-evident", which was true and
    honest. The chain changed that, so the assertion is now that the note states the
    limits that remain — a stronger claim must not come with a vaguer disclaimer."""
    summary = audit.summary()
    note = summary["integrity"].lower()
    assert "truncation" in note, "must not imply the chain detects truncation alone"
    assert "compromised application process" in note
    assert "accesses_without_stated_reason" in summary
    assert summary["chain"]["head"], "the summary must expose the chain head"


def test_the_audit_chain_verifies_over_the_live_log(client):
    """Whatever this test run wrote to the real sink must itself verify. A chain that
    only holds in a fixture is not protecting anything."""
    result = audit.verify_chain()
    assert result["intact"] is True, result.get("reason")


def test_chain_verification_is_supervisor_only_and_self_recording(client):
    """Same rule as /audit-log: a privileged read that leaves no trace is half a
    control, and the integrity check is exactly the call an attacker would want to
    make quietly before deciding whether their edit was noticed."""
    assert client.get(f"{API}/audit-log/verify").status_code == 403
    assert client.get(f"{API}/audit-log/verify",
                      headers=bearer("investigator")).status_code == 403

    r = client.get(f"{API}/audit-log/verify", headers=bearer("supervisor"))
    assert r.status_code == 200
    assert r.get_json()["intact"] is True
    assert any(e["action"] == "verify_audit_chain" for e in audit.recent(limit=20))


# ------------------------------------------------------------------ error paths
def test_unknown_route_is_not_served(client):
    # 405 rather than 404 because the CORS catch-all rule `/<path:path>` matches any
    # path for OPTIONS, so Flask reports a method mismatch instead of a missing rule.
    # Confusing, but not a disclosure; the global JSON error handler will normalise it.
    assert client.get(f"{API}/no-such-endpoint").status_code in (404, 405)


def test_unknown_case_id_is_handled(client):
    r = client.get(f"{API}/case/NOT-A-REAL-CASE", headers=bearer("investigator"))
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.get_json() is not None


def test_classify_rejects_an_empty_body(client):
    r = client.post(f"{API}/classify", json={})
    assert r.status_code == 200
    assert r.get_json()["predictions"] == []


# ------------------------------------------------------------------ LLM PII egress
def test_names_are_aliased_before_leaving_the_process(client):
    """The copilot injects real offender records into prompts POSTed to Groq/NVIDIA.
    Those are third-party services outside India; a name must not reach them."""
    with app.test_request_context("/"):
        alias = privacy.mask("Kartik Bhartiya")
        assert alias.startswith("[PERSON_")
        assert "Kartik" not in alias


def test_aliases_are_stable_within_a_request(client):
    """The model needs referential consistency — the same person twice in one prompt
    must be the same token, or its reasoning breaks."""
    with app.test_request_context("/"):
        assert privacy.mask("Rekha Patil") == privacy.mask("Rekha Patil")
        assert privacy.mask("Rekha Patil") != privacy.mask("Kiran Gowda")


def test_aliases_do_not_survive_the_request():
    """The alias table must not outlive the request that built it.

    Flask binds `g` to the *application* context, and `test_request_context` reuses
    an app context that is already pushed — the module-scoped client fixture keeps
    one alive. So each half here pushes its own app context explicitly; without
    that, both halves share one `g` and the test proves nothing.
    """
    with app.app_context():
        with app.test_request_context("/"):
            privacy.mask("Kartik Bhartiya")
            assert privacy.active_aliases() == 1

    with app.app_context():
        with app.test_request_context("/"):
            assert privacy.active_aliases() == 0, "aliases leaked across requests"


def test_answers_are_restored_locally(client):
    """Aliasing must be invisible to the user, or it gets switched off."""
    with app.test_request_context("/"):
        alias = privacy.mask("Kartik Bhartiya")
        assert privacy.restore(f"{alias} is the highest risk.") ==             "Kartik Bhartiya is the highest risk."


def test_restore_leaves_unknown_aliases_alone(client):
    with app.test_request_context("/"):
        assert privacy.restore("[PERSON_9] unknown") == "[PERSON_9] unknown"


# ------------------------------------------------------------------ load safety
def test_concurrent_loads_are_serialised(client):
    """Regression: `_warm_lock` guarded only `check_ready`, so `/sync`, the warm-up
    thread and any direct call could run a full load concurrently in one process.

    The loader cleared the shared knowledge graph and repopulated it in place, so an
    overlapping load crashed with `KeyError` inside `G.add_edge` — and a `/network`
    request landing at the same moment would have observed an empty or half-built
    graph and reported it as fact.
    """
    import threading

    import main as m

    errors = []
    before_nodes = m.knowledge_graph.number_of_nodes()

    def hammer():
        try:
            # force=True is essential. Without it every thread takes the
            # `_load_complete` early return and the test passes even with the lock
            # deleted — it would assert nothing about the race it is named for.
            m.load_and_build_intelligence(force=True)
        except Exception as e:      # pragma: no cover - the bug being guarded
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=600)

    assert not errors, f"concurrent loads raised: {errors}"
    assert m.knowledge_graph.number_of_nodes() == before_nodes, (
        "the graph changed size under concurrent loads")


def test_the_graph_is_never_published_empty(client):
    """The graph is built into a fresh object and the global rebound once complete,
    so a reader always holds a fully-built graph."""
    import main as m
    assert m.knowledge_graph.number_of_nodes() > 0
    assert m.knowledge_graph.number_of_edges() > 0


def test_a_redundant_load_is_skipped_not_repeated(client):
    """A caller queued behind a load that just finished must not repeat it."""
    import main as m
    assert m.data_cache.get("_load_complete") is True
    assert m.load_and_build_intelligence() is True


# ------------------------------------------------- review regressions (2026-08-27)
def test_no_raw_fetch_bypasses_the_auth_header():
    """Static guard: every API call must go through getJSON / apiFetch.

    Gating 16 routes server-side broke the clients that called them with a bare
    `fetch`, because `authHeaders()` was referenced in exactly one place. The copilot,
    the network page, the cases page and settings all returned 403 to signed-in users.
    No runtime test could catch it — `test_api.py` builds its own bearer header — so
    the guard has to be on the source.
    """
    import pathlib
    import re

    web = pathlib.Path(__file__).resolve().parents[2] / "web" / "src"
    if not web.exists():
        pytest.skip("web sources not present")

    offenders = []
    raw_call = re.compile(r"(?<!api)(?<!\w)fetch\(\s*(api\(|`\$\{API_URL\})")
    for path in list(web.rglob("*.tsx")) + list(web.rglob("*.ts")):
        if path.name == "api.ts":
            continue        # the one place a raw fetch belongs
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if raw_call.search(line):
                offenders.append(f"{path.relative_to(web)}:{i}")
    assert not offenders, (
        "raw fetch() bypassing authHeaders(): " + ", ".join(offenders))


def test_a_denied_request_is_audited(client):
    """Regression: @auth.require_role was listed ABOVE @audit.audited, so its 403
    returned before the audit wrapper ran and no denial was ever recorded — the
    audit trail exists to show who was refused, not only who succeeded."""
    before = len(audit.recent(limit=2000))
    r = client.get(f"{API}/suspect/SUS-1001")          # no token
    assert r.status_code == 403

    entries = audit.recent(limit=2000)
    assert len(entries) > before, "an authorization denial left no trace"
    denial = entries[0]
    assert denial["action"] == "read_offender_dossier"
    assert denial["result"] == "denied"
    assert denial["status"] == 403
    assert denial["subject_id"] == "SUS-1001"


def test_an_invented_crimeno_does_not_resolve(client):
    """The dangerous shape is the long one. normalize_case_id returned any 15-20
    digit string verbatim, so an LLM-invented CrimeNo routed straight to open_case —
    exactly what grounding.PATTERNS flags as the hallucination that matters."""
    import main as m
    real = str(m.data_cache["firs"].iloc[0]["case_id"])
    assert m.normalize_case_id(real) == real
    assert m.normalize_case_id("100060019209900001") is None


# ------------------------------------------------------------- feedback loop
def test_recording_an_outcome_requires_a_role(client):
    """Recording an outcome states a fact about a named individual, and a wrong entry
    corrupts every metric computed from it afterwards."""
    r = client.post(f"{API}/models/outcome", json={
        "model": "conviction_outcome", "subject_id": "SUS-1001",
        "outcome": "convicted"})
    assert r.status_code == 403


def test_realised_performance_requires_a_role(client):
    """A table showing a model performing worse than published should not be
    anonymously scrapable."""
    assert client.get(f"{API}/models/performance").status_code == 403


def test_an_uninterpretable_outcome_is_refused_over_http(client):
    r = client.post(f"{API}/models/outcome",
                    headers=bearer("investigator"),
                    json={"model": "conviction_outcome",
                          "subject_id": "SUS-1001", "outcome": "probably"})
    assert r.status_code == 400
    body = r.get_json()
    assert "convicted" in body["accepted"]["conviction_outcome"]


def test_an_incomplete_outcome_is_refused(client):
    r = client.post(f"{API}/models/outcome", headers=bearer("investigator"),
                    json={"model": "conviction_outcome"})
    assert r.status_code == 400


def test_recording_an_outcome_is_audited(client, tmp_path, monkeypatch):
    """Same rule as every other person-level action: it leaves a trace naming who
    recorded what about whom."""
    import importlib

    monkeypatch.setenv("OUTCOME_LOG_PATH", str(tmp_path / "outcomes.jsonl"))
    from ml import outcomes as outcomes_module
    importlib.reload(outcomes_module)

    r = client.post(f"{API}/models/outcome", headers=bearer("investigator"),
                    json={"model": "conviction_outcome",
                          "subject_id": "SUS-1001", "outcome": "convicted"})
    assert r.status_code == 201
    assert r.get_json()["recorded"]["label"] == 1

    entries = audit.recent(limit=20)
    assert any(e["action"] == "record_outcome" and e["subject_id"] == "SUS-1001"
               for e in entries)

    monkeypatch.undo()
    importlib.reload(outcomes_module)


def test_performance_reports_an_empty_loop_honestly(client):
    """With nothing recorded the response must say so per model. Zeros here would
    read as perfect scores."""
    r = client.get(f"{API}/models/performance", headers=bearer("investigator"))
    assert r.status_code == 200
    body = r.get_json()
    assert "conviction_outcome" in body["models"]
    assert "coverage" in body

    for name, report in body["models"].items():
        if report.get("matured", 0) == 0:
            assert report["verdict"] == "no outcomes recorded yet", name
            assert "brier" not in report, f"{name} reported a metric with no data"


def test_performance_rejects_a_model_with_no_outcome_vocabulary(client):
    """hotspot_clusters is unsupervised — there is no outcome to record against it,
    and pretending otherwise would invite meaningless entries."""
    r = client.get(f"{API}/models/performance?model=hotspot_clusters",
                   headers=bearer("investigator"))
    assert r.status_code == 400


# ----------------------------------------------------------------- retention
def test_retention_policy_is_supervisor_only(client):
    """It enumerates every category of personal data the system holds."""
    assert client.get(f"{API}/retention").status_code == 403
    assert client.get(f"{API}/retention",
                      headers=bearer("investigator")).status_code == 403

    r = client.get(f"{API}/retention", headers=bearer("supervisor"))
    assert r.status_code == 200
    body = r.get_json()
    for sink in ("audit", "predictions", "outcomes"):
        assert body[sink]["justification"], f"{sink} has no stated justification"


def test_purge_and_erase_are_previews_unless_explicitly_applied(client):
    """Deleting personal data is correct and irreversible at the same time, so the
    destructive path is never the default one."""
    r = client.post(f"{API}/retention/purge", headers=bearer("supervisor"), json={})
    assert r.status_code == 200 and r.get_json()["dry_run"] is True

    r = client.post(f"{API}/retention/erase", headers=bearer("supervisor"),
                    json={"subject_id": "SUS-1001"})
    assert r.status_code == 200 and r.get_json()["dry_run"] is True


def test_erasure_reports_what_it_deliberately_did_not_erase(client):
    """An erasure report that quietly omitted the retained access log would
    misrepresent what the subject was actually granted."""
    r = client.post(f"{API}/retention/erase", headers=bearer("supervisor"),
                    json={"subject_id": "SUS-1001"})
    body = r.get_json()
    assert "audit" in body["retained"]
    assert "official conduct" in body["retained_because"]


def test_erasure_and_purge_require_a_supervisor(client):
    assert client.post(f"{API}/retention/purge", json={}).status_code == 403
    assert client.post(f"{API}/retention/erase",
                       headers=bearer("investigator"),
                       json={"subject_id": "SUS-1001"}).status_code == 403


def test_purging_an_unknown_sink_is_refused(client):
    r = client.post(f"{API}/retention/purge", headers=bearer("supervisor"),
                    json={"sink": "nonexistent"})
    assert r.status_code == 400


# ------------------------------------------------------------ contestability
def test_raising_a_contest_requires_a_role_and_grounds(client):
    assert client.post(f"{API}/contest",
                       json={"subject_id": "SUS-1001",
                             "grounds": "x"}).status_code == 403

    r = client.post(f"{API}/contest", headers=bearer("investigator"),
                    json={"subject_id": "SUS-1001"})
    assert r.status_code == 400, "a challenge with no stated basis cannot be reviewed"


def test_the_subject_report_is_supervisor_only(client):
    """The most concentrated view of one individual the platform can produce, which
    makes it the most valuable to misuse."""
    assert client.get(f"{API}/subject-report/SUS-1001").status_code == 403
    assert client.get(f"{API}/subject-report/SUS-1001",
                      headers=bearer("investigator")).status_code == 403

    r = client.get(f"{API}/subject-report/SUS-1001", headers=bearer("supervisor"))
    assert r.status_code == 200
    body = r.get_json()
    assert "who_accessed_this_record" in body
    assert "decision support" in body["note"]
    assert any(e["action"] == "read_subject_report" for e in audit.recent(limit=20))


def test_a_contested_score_reaches_the_officer_marked(client, tmp_path, monkeypatch):
    """The property that makes contestability a control rather than a complaints box.

    The score is deliberately not suppressed — adjudication is the deploying
    authority's decision — but it never reaches an officer without the fact that it
    is disputed. Exercised through the real dossier endpoint, because that is where
    it has to hold.
    """
    import importlib

    monkeypatch.setenv("CONTEST_LOG_PATH", str(tmp_path / "contests.jsonl"))
    import contest as contest_module
    contest_module = importlib.reload(contest_module)

    subject = None
    for candidate in ("SUS-1001", "SUS-1002", "SUS-1003", "SUS-1005", "SUS-1010"):
        body = client.get(f"{API}/suspect/{candidate}",
                          headers=bearer("investigator")).get_json() or {}
        if body.get("ml_conviction_basis"):
            subject = candidate
            break
    if subject is None:
        pytest.skip("no scored subject in the sample")

    before = client.get(f"{API}/suspect/{subject}",
                        headers=bearer("investigator")).get_json()
    assert before["ml_conviction_basis"]["contested"] is False

    r = client.post(f"{API}/contest", headers=bearer("investigator"),
                    json={"subject_id": subject, "model": "conviction_outcome",
                          "grounds": "the prior arrest was of a different person"})
    assert r.status_code == 201

    after = client.get(f"{API}/suspect/{subject}",
                       headers=bearer("investigator")).get_json()
    assert after["ml_conviction_basis"]["contested"] is True
    assert after["ml_conviction_basis"]["contested_note"]
    # The score itself is unchanged: filing a challenge is not adjudicating it.
    assert (after["ml_conviction_probability"]
            == before["ml_conviction_probability"])

    monkeypatch.undo()
    importlib.reload(contest_module)


def test_resolving_a_contest_is_supervisor_only(client):
    assert client.post(f"{API}/contest/resolve", headers=bearer("investigator"),
                       json={"contest_id": "x", "subject_id": "y",
                             "status": "upheld"}).status_code == 403

    r = client.post(f"{API}/contest/resolve", headers=bearer("supervisor"),
                    json={"contest_id": "x", "subject_id": "y", "status": "maybe"})
    assert r.status_code == 400
    assert "upheld" in r.get_json()["accepted_statuses"]


def test_the_open_contest_queue_is_visible(client):
    """A queue nobody can see is a queue nobody works."""
    assert client.get(f"{API}/contests").status_code == 403
    r = client.get(f"{API}/contests", headers=bearer("supervisor"))
    assert r.status_code == 200
    assert "open" in r.get_json()


# --------------------------------------------------------------- replay route
def test_replay_is_supervisor_only(client):
    """It writes to the prediction and outcome sinks, which are the runtime record."""
    assert client.post(f"{API}/models/replay", json={}).status_code == 403
    assert client.post(f"{API}/models/replay", headers=bearer("investigator"),
                       json={}).status_code == 403


def test_replay_previews_unless_applied(client):
    """Same rule as purge and erase: the write is never the default."""
    r = client.post(f"{API}/models/replay", headers=bearer("supervisor"),
                    json={"limit": 5})
    assert r.status_code == 200
    body = r.get_json()
    assert body["applied"] is False
    for name, result in body["models"].items():
        assert result.get("written") == 0, name


def test_replay_confirms_the_serving_path_matches_offline_evaluation(client):
    """The check that justifies the whole thing.

    Offline metrics are computed inside the training script; serving loads a joblib
    and scores a frame rebuilt elsewhere. When those diverge the model still returns
    numbers — they are just quietly worse — so nothing fails and nobody notices. This
    asserts they agree.
    """
    r = client.post(f"{API}/models/replay", headers=bearer("supervisor"),
                    json={"limit": 5})
    body = r.get_json()
    for name, result in body["models"].items():
        skew = result.get("skew")
        if not skew:
            continue
        assert skew["verdict"].startswith("serving path reproduces"), (
            f"{name}: {skew['verdict']} (published {skew['roc_auc_published']}, "
            f"served {skew['roc_auc_served']})")


def test_a_write_limit_does_not_change_the_skew_comparison(client):
    """Regression: `limit` used to truncate the scored set as well as the written
    one, so a sampled ROC-AUC was compared against a metric computed on the full
    held-out set and the check reported training/serving skew that did not exist.

    A check that cries wolf is worse than no check, so the limit now bounds only the
    writes and the comparison always runs on everything.
    """
    small = client.post(f"{API}/models/replay", headers=bearer("supervisor"),
                        json={"limit": 5}).get_json()["models"]
    large = client.post(f"{API}/models/replay", headers=bearer("supervisor"),
                        json={"limit": 500}).get_json()["models"]
    for name in small:
        assert small[name]["served"]["n"] == large[name]["served"]["n"], (
            f"{name}: the scored set changed with the write limit")
        assert small[name]["served"]["roc_auc"] == large[name]["served"]["roc_auc"]


def test_replay_rejects_a_model_it_has_no_plan_for(client):
    r = client.post(f"{API}/models/replay", headers=bearer("supervisor"),
                    json={"model": "hotspot_clusters"})
    assert r.status_code == 400
    assert "available" in r.get_json()
