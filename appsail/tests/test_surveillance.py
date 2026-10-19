"""CCTV & ANPR matching, the alert queue, and the copilot's use of them.

The matcher is the part of this feature that makes a claim about a person, so the
tests pin its measured behaviour — a registered face is found, a stranger mostly is
not, a mangled plate still matches — and the controls around it: a stranger's face is
never identified in the feed, and nobody can mark a match verified without saying why.
"""
import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import surveillance as S  # noqa: E402

API = "/serverless/api"


@pytest.fixture(scope="module", autouse=True)
def loaded():
    main.load_and_build_intelligence()
    S.ensure(main.data_cache)


@pytest.fixture(scope="module")
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


@pytest.fixture(scope="module")
def token(client):
    r = client.post(f"{API}/auth/login", json={"username": "io", "password": "scrb-io"})
    return r.get_json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ network
def test_every_district_has_cameras_and_bengaluru_has_the_most():
    summary = S.cameras(main.data_cache)["summary"]
    by = summary["by_district"]
    assert max(by, key=by.get) == "Bengaluru Urban"
    assert "Vijayanagara" in by and "Mangaluru" not in by, "commissionerate leaked in as a district"


# ------------------------------------------------------------------ face matching
def test_a_clear_sighting_of_a_registered_face_is_identified():
    rng = np.random.default_rng(1)
    sid = S._state["gallery_ids"][123]
    m = S.match_face(S._probe_for(sid, 0.95, rng))
    assert m["band"] == "strong" and m["candidates"][0]["suspect_id"] == sid


def test_the_measured_error_rates_are_within_the_stated_operating_point():
    ev = S.evaluate(main.data_cache)["face"]
    assert ev["false_positive_identification_rate"] < 0.01
    assert ev["true_identification_rate"] > 0.75
    assert ev["tir_low_quality_or_night"] < ev["tir_good_quality"], "night should be harder, and say so"


def test_ocr_confusions_still_match_a_stolen_plate():
    plate = next(iter(S._state["plates"]))
    mangled = plate.replace("0", "O", 1).replace("1", "I", 1) if ("0" in plate or "1" in plate) else plate[:-1] + "B"
    m = S.match_plate(mangled)
    assert m and S.normalize_plate(m["vehicle_number"]) == plate


def test_a_random_plate_does_not_match():
    assert S.match_plate("KA99ZZ0000") is None


# ------------------------------------------------------------------ feed and alerts
def test_feed_never_identifies_a_stranger(token, client):
    r = client.get(f"{API}/surveillance/feed?limit=300", headers=_auth(token))
    assert r.status_code == 200
    for e in r.get_json()["events"]:
        if e["kind"] == "face" and e["face_match"] is None:
            assert "person" not in e and "suspect_id" not in e


def test_person_level_surveillance_routes_need_investigator(client):
    for route in ("/surveillance/feed", "/surveillance/alerts", "/surveillance/track/SUS-8286",
                  "/surveillance/plate?q=KA01", "/surveillance/watchlist"):
        assert client.get(f"{API}{route}").status_code == 403, route
    assert client.get(f"{API}/surveillance/cameras").status_code == 200


def test_verifying_a_match_requires_a_reason_and_is_recorded(token, client):
    alerts = client.get(f"{API}/surveillance/alerts", headers=_auth(token)).get_json()["alerts"]
    assert alerts, "a day of a 590-camera network produced no alerts"
    aid = alerts[0]["event_id"]
    r = client.post(f"{API}/surveillance/alerts/{aid}", json={"action": "verify"}, headers=_auth(token))
    assert r.status_code == 400
    r = client.post(f"{API}/surveillance/alerts/{aid}",
                    json={"action": "verify", "note": "compared with dossier photo and tattoo"},
                    headers=_auth(token))
    assert r.status_code == 200 and r.get_json()["alert"]["status"] == "verified"
    assert r.get_json()["alert"]["decision"]["actor"] == "io"


def test_a_watchlisted_person_is_raised_to_p1(token, client):
    alerts = client.get(f"{API}/surveillance/alerts?priority=P2", headers=_auth(token)).get_json()["alerts"]
    face = next((a for a in alerts if a.get("face_match")), None)
    if face is None:
        pytest.skip("no P2 face alert in this window")
    sid = face["face_match"]["suspect_id"]
    client.post(f"{API}/surveillance/watchlist", json={"add": sid}, headers=_auth(token))
    again = client.get(f"{API}/surveillance/alerts", headers=_auth(token)).get_json()["alerts"]
    same = next(a for a in again if a["event_id"] == face["event_id"])
    assert same["priority"] == "P1" and "On your watchlist" in same["reasons"]
    client.post(f"{API}/surveillance/watchlist", json={"remove": sid}, headers=_auth(token))


def test_the_stream_is_the_same_for_every_process():
    b = int(time.time() // S.BLOCK_S)
    first = [(e["event_id"], e["camera_id"]) for e in S._block(b)]
    S._state["blocks"].pop(b)
    assert [(e["event_id"], e["camera_id"]) for e in S._block(b)] == first


# ------------------------------------------------------------------ copilot
@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(main, "llm_available", lambda: False)


def _ask(q, history=None):
    with main.app.test_request_context("/"):
        return main.run_agent(q, history=history)


def test_an_open_question_about_a_city_gets_a_situation_brief(offline):
    r = _ask("whats going on in bangalore rn")
    assert "Bengaluru Urban right now" in r["reply"]


def test_a_spoken_city_name_filters_the_case_list(offline):
    r = _ask("show me the latest chain snatching cases in bangalore")
    assert r["action"] == "recent_cases"
    assert r["params"] == {"crime_type": "Chain Snatching", "district": "Bengaluru Urban", "sort": "recent"}


def test_follow_ups_inherit_the_conversation(offline):
    h = [{"role": "user", "content": "how many murders in Mysuru in 2024?"},
         {"role": "assistant", "content": "17 Murder in Mysuru during 2024"}]
    r = _ask("what about Belagavi?", history=h)
    assert "Belagavi" in r["reply"] and "2024" in r["reply"] and "Murder" in r["reply"]
    r = _ask("show them on the map", history=h)
    assert r["action"] == "show_heatmap" and r["params"].get("district") == "Mysuru"


def test_last_seen_is_answered_from_the_camera_network(offline):
    r = _ask("where was SUS-8286 last seen?")
    assert r["action"] == "answer"
    assert "SUS-8286" in r["reply"] and ("matched" in r["reply"] or "last matched" in r["reply"])


def test_nothing_dead_ends(offline):
    r = _ask("what is the weather today")
    assert r["reply"] and r["action"] == "answer"


# ------------------------------------------------------------------ saved conversations
def test_a_conversation_keeps_its_own_context(token, client):
    h = _auth(token)
    c = client.post(f"{API}/copilot/conversations", json={}, headers=h).get_json()
    r = client.post(f"{API}/copilot/conversations/{c['id']}/messages",
                    json={"content": "how many murders in Mysuru in 2024?"}, headers=h).get_json()
    assert "Mysuru" in r["message"]["content"]
    assert r["conversation"]["title"].startswith("How many murders in Mysuru")
    r = client.post(f"{API}/copilot/conversations/{c['id']}/messages",
                    json={"content": "what about Belagavi?"}, headers=h).get_json()
    assert "Belagavi" in r["message"]["content"] and "2024" in r["message"]["content"]
    listed = client.get(f"{API}/copilot/conversations", headers=h).get_json()["conversations"]
    assert any(x["id"] == c["id"] and x["messages"] == 4 for x in listed)
    assert client.post(f"{API}/copilot/conversations/{c['id']}", json={"delete": True}, headers=h).status_code == 200
    assert client.options(f"{API}/copilot/conversations/{c['id']}/messages").status_code == 204


def test_conversations_are_private_to_their_owner(token, client):
    h = _auth(token)
    c = client.post(f"{API}/copilot/conversations", json={"title": "mine"}, headers=h).get_json()
    sp = client.post(f"{API}/auth/login", json={"username": "sp", "password": "scrb-sp"}).get_json()["token"]
    assert client.get(f"{API}/copilot/conversations/{c['id']}", headers=_auth(sp)).status_code == 404
    assert client.get(f"{API}/copilot/conversations").status_code == 403
