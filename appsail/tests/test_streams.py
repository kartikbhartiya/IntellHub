"""Live stream sources attached to cameras (streams.py).

A source is a URL handed to every investigator's browser, so the tests pin what may
never get through - plain http to the internet, credentials, RTSP, an unknown camera -
and the controls around changing one: a supervisor only, audited with the camera as
the subject, persisted atomically and read back exactly.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import streams  # noqa: E402
import surveillance  # noqa: E402

API = "/serverless/api"
URL = f"{API}/surveillance/streams"


@pytest.fixture(scope="module", autouse=True)
def loaded(tmp_path_factory):
    os.environ["CAMERA_STREAMS_PATH"] = str(tmp_path_factory.mktemp("streams") / "camera_streams.json")
    os.environ.pop("CAMERA_STREAMS", None)
    main.load_and_build_intelligence()
    streams.configure(lambda: {c["camera_id"] for c in surveillance.ensure(main.data_cache)["cameras"]})
    if "camera_streams" not in main.app.blueprints:
        # Other modules in the same session may already have served requests through
        # main.app, after which Flask refuses new blueprints. Once main.py registers
        # this one itself the branch is never taken.
        served = main.app._got_first_request
        main.app._got_first_request = False
        try:
            main.app.register_blueprint(streams.bp)
        finally:
            main.app._got_first_request = served
    yield
    os.environ.pop("CAMERA_STREAMS_PATH", None)


@pytest.fixture(autouse=True)
def clean_store():
    path = os.environ["CAMERA_STREAMS_PATH"]
    if os.path.exists(path):
        os.remove(path)
    os.environ.pop("CAMERA_STREAMS", None)
    yield
    os.environ.pop("CAMERA_STREAMS", None)


@pytest.fixture(scope="module")
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def _token(client, user, pw):
    r = client.post(f"{API}/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['token']}"}


@pytest.fixture(scope="module")
def io(client):
    return _token(client, "io", "scrb-io")


@pytest.fixture(scope="module")
def sp(client):
    return _token(client, "sp", "scrb-sp")


@pytest.fixture(scope="module")
def cam():
    return surveillance.ensure(main.data_cache)["cameras"][0]["camera_id"]


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("url", [
    "https://nvr.example.in/live/cam1/index.m3u8",
    "https://cdn.example.in/hls/cam.m3u8?token=abc123&expires=1760000000",
    "http://192.168.1.20:8888/cam1/index.m3u8",
    "http://10.4.0.7/stream.mjpg",
    "http://172.20.3.4:8889/gate/whep",
    "http://localhost:8888/cam/index.m3u8",
    "http://nvr-blr-03.local/hls/1.m3u8",
    "http://nvr-blr-03/hls/1.m3u8",
    "http://[fd12::5]:8080/cam.m3u8",
])
def test_acceptable_urls_pass(url):
    assert streams.check_url(url) == url


@pytest.mark.parametrize("url, why", [
    ("http://nvr.example.in/cam.m3u8", "http:// to an internet host"),
    ("http://8.8.8.8/cam.m3u8", "http:// to a public IP"),
    ("http://172.32.0.1/cam.m3u8", "just outside 172.16/12"),
    ("https://admin:hunter2@nvr.example.in/cam.m3u8", "userinfo credentials"),
    ("https://:hunter2@nvr.example.in/cam.m3u8", "password with an empty user"),
    ("https://nvr.example.in/cam.m3u8?user=admin&password=x", "credential query parameters"),
    ("https://nvr.example.in/cam.m3u8?api-key=x", "an api key"),
    ("rtsp://192.168.1.20:554/Streaming/Channels/101", "rtsp is not browser-playable"),
    ("javascript:alert(1)", "not a web URL"),
    ("ftp://nvr.example.in/x", "not a web URL"),
    ("https:///nohost", "no host"),
    ("https://nvr.example.in/a b.m3u8", "whitespace"),
    ("https://nvr.example.in:99999/x", "bad port"),
    ("https://nvr.example.in/" + "a" * 2100, "too long"),
    ("", "empty"),
])
def test_unacceptable_urls_are_refused(url, why):
    with pytest.raises(streams.Invalid) as e:
        streams.check_url(url)
    assert e.value.field == "url", why


def test_rtsp_gets_an_answer_that_says_what_to_do():
    with pytest.raises(streams.Invalid) as e:
        streams.check_url("rtsp://10.0.0.5/live")
    assert "gateway" in e.value.message and "WHEP" in e.value.message


@pytest.mark.parametrize("url, vid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1", "dQw4w9WgXcQ"),
])
def test_youtube_links_normalise_to_a_video_id(url, vid):
    assert streams.youtube_id(url) == vid
    rec = streams.validate({"kind": "youtube", "url": url})
    assert rec["video_id"] == vid
    assert rec["url"] == f"https://www.youtube.com/watch?v={vid}"


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/@SomeChannel/live",
    "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/live",
    "https://www.youtube.com/watch?v=short",
    "https://evil.example/watch?v=dQw4w9WgXcQ",
    "https://youtu.be.evil.example/dQw4w9WgXcQ",
])
def test_non_video_youtube_links_are_refused(url):
    assert streams.youtube_id(url) is None
    with pytest.raises(streams.Invalid):
        streams.validate({"kind": "youtube", "url": url})


def test_youtube_link_under_another_kind_is_pointed_at_the_right_kind():
    with pytest.raises(streams.Invalid) as e:
        streams.validate({"kind": "hls", "url": "https://youtu.be/dQw4w9WgXcQ"})
    assert e.value.field == "kind"


def test_device_takes_no_url_and_other_kinds_need_one():
    assert streams.validate({"kind": "device"})["url"] is None
    with pytest.raises(streams.Invalid):
        streams.validate({"kind": "device", "url": "https://nvr.example.in/x.m3u8"})
    for kind in ("hls", "mp4", "mjpeg", "webrtc-whep", "youtube"):
        with pytest.raises(streams.Invalid) as e:
            streams.validate({"kind": kind})
        assert e.value.field == "url"


def test_unknown_kind_and_bad_text_are_refused():
    with pytest.raises(streams.Invalid) as e:
        streams.validate({"kind": "rtsp", "url": "https://x.example/y"})
    assert e.value.field == "kind"
    with pytest.raises(streams.Invalid) as e:
        streams.validate({"kind": "hls", "url": "https://x.example/y.m3u8", "label": "x" * 121})
    assert e.value.field == "label"
    with pytest.raises(streams.Invalid) as e:
        streams.validate({"kind": "hls", "url": "https://x.example/y.m3u8", "attribution": "a\nb"})
    assert e.value.field == "attribution"


# ------------------------------------------------------------------ roles
def test_reading_needs_an_investigator(client, io):
    assert client.get(URL).status_code == 403
    r = client.get(URL, headers=io)
    assert r.status_code == 200
    body = r.get_json()
    assert body["streams"] == {} and body["can_edit"] is False
    assert [k["kind"] for k in body["kinds"]] == streams.KIND_IDS
    assert "storage" not in body and "history" not in body, "provenance is for supervisors"


def test_changing_needs_a_supervisor(client, io, cam):
    payload = {"camera_id": cam, "kind": "hls", "url": "https://nvr.example.in/a.m3u8"}
    assert client.post(URL, json=payload).status_code == 403
    assert client.post(URL, json=payload, headers=io).status_code == 403
    assert not os.path.exists(os.environ["CAMERA_STREAMS_PATH"])


def test_preflight_is_not_gated(client):
    assert client.open(URL, method="OPTIONS").status_code in (200, 204)


# ------------------------------------------------------------------ write path
def test_supervisor_sets_and_everyone_signed_in_sees_it(client, io, sp, cam):
    r = client.post(URL, headers=sp, json={
        "camera_id": cam, "kind": "hls", "url": "https://nvr.example.in/live/a.m3u8",
        "label": "  Majestic gate NVR  ", "attribution": "BTP Traffic Management Centre"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] and body["camera_id"] == cam and body["can_edit"] is True
    s = body["streams"][cam]
    assert s["kind"] == "hls" and s["label"] == "Majestic gate NVR"
    assert s["added_by"] == "sp" and s["added_at"].endswith("Z") and s["origin"] == "operator"
    assert body["history"][0]["url"] == "https://nvr.example.in/live/a.m3u8"

    seen = client.get(URL, headers=io).get_json()["streams"][cam]
    assert seen["url"] == "https://nvr.example.in/live/a.m3u8"


def test_validation_errors_come_back_per_field(client, sp, cam):
    r = client.post(URL, headers=sp, json={"camera_id": cam, "kind": "hls",
                                            "url": "https://admin:pw@nvr.example.in/a.m3u8"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "invalid_stream" and "url" in body["errors"]
    r = client.post(URL, headers=sp, data="not json", content_type="text/plain")
    assert r.status_code == 400


def test_unknown_camera_is_refused(client, sp):
    r = client.post(URL, headers=sp, json={"camera_id": "CAM-NOPE-9999", "kind": "device"})
    assert r.status_code == 404
    assert r.get_json()["errors"]["camera_id"]
    r = client.post(URL, headers=sp, json={"kind": "device"})
    assert r.status_code == 400 and "camera_id" in r.get_json()["errors"]


def test_persistence_round_trip_and_atomic_file(client, sp, cam):
    client.post(URL, headers=sp, json={"camera_id": cam, "kind": "youtube",
                                       "url": "https://youtu.be/dQw4w9WgXcQ", "attribution": "Some channel"})
    path = os.environ["CAMERA_STREAMS_PATH"]
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["streams"][cam]["video_id"] == "dQw4w9WgXcQ"
    assert doc["streams"][cam]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    leftovers = [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]
    assert leftovers == [], "the temp file must be renamed into place, not left behind"

    # A fresh read from disk (as after a restart) gives back the same record.
    streams._file_cache.update(sig=None, doc=None, error=None)
    again = streams.snapshot()["streams"][cam]
    assert again["kind"] == "youtube" and again["video_id"] == "dQw4w9WgXcQ"

    r = client.post(URL, headers=sp, json={"camera_id": cam, "remove": True})
    assert r.status_code == 200 and r.get_json()["removed"] is True
    assert cam not in streams.snapshot()["streams"]
    with open(path, encoding="utf-8") as fh:
        assert cam not in json.load(fh)["streams"]


def test_hand_edited_file_cannot_bypass_the_rules(sp, cam):
    path = os.environ["CAMERA_STREAMS_PATH"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "history": [], "streams": {
            cam: {"kind": "hls", "url": "http://public.example.in/a.m3u8"},
            "CAM-GONE-0001": {"kind": "device"}}}, fh)
    snap = streams.snapshot(include_admin=True)
    assert snap["streams"] == {}
    assert {i["camera_id"] for i in snap["ignored"]} == {cam, "CAM-GONE-0001"}


def test_unreadable_file_is_set_aside_not_overwritten(client, sp, cam):
    path = os.environ["CAMERA_STREAMS_PATH"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    assert streams.snapshot(include_admin=True)["storage"]["file_error"]
    r = client.post(URL, headers=sp, json={"camera_id": cam, "kind": "device"})
    assert r.status_code == 200
    folder = os.path.dirname(path)
    assert any(".unreadable-" in f for f in os.listdir(folder))
    for f in os.listdir(folder):
        if ".unreadable-" in f:
            os.remove(os.path.join(folder, f))


def test_deployment_defaults_are_read_only(client, io, sp, cam):
    os.environ["CAMERA_STREAMS"] = json.dumps({
        cam: {"kind": "mjpeg", "url": "http://192.168.1.50/video.mjpg", "label": "Gate camera"},
        "CAM-GONE-0002": {"kind": "device"},
    })
    body = client.get(URL, headers=io).get_json()
    assert body["streams"][cam]["origin"] == "deployment"
    assert "CAM-GONE-0002" not in body["streams"]

    r = client.post(URL, headers=sp, json={"camera_id": cam, "remove": True})
    assert r.status_code == 409 and r.get_json()["error"] == "deployment_default"

    # An operator override wins, and removing it restores the deployment's source.
    client.post(URL, headers=sp, json={"camera_id": cam, "kind": "device"})
    s = client.get(URL, headers=io).get_json()["streams"][cam]
    assert s["kind"] == "device" and s["overrides_default"] is True
    client.post(URL, headers=sp, json={"camera_id": cam, "remove": True})
    assert client.get(URL, headers=io).get_json()["streams"][cam]["kind"] == "mjpeg"


def test_malformed_deployment_json_is_reported_not_fatal(client, sp):
    os.environ["CAMERA_STREAMS"] = "{not json"
    r = client.get(URL, headers=sp)
    assert r.status_code == 200
    assert any("not valid JSON" in i["reason"] for i in r.get_json()["ignored"])


def test_changes_are_audited_with_the_camera_as_subject(client, io, sp, cam):
    import audit
    client.post(URL, headers=sp, json={"camera_id": cam, "kind": "device"})
    client.post(URL, headers=io, json={"camera_id": cam, "kind": "device"})
    entries = [e for e in audit.recent(50) if e["action"] == "set_camera_stream"]
    assert entries[0]["subject_id"] == cam and entries[0]["result"] == "denied" and entries[0]["actor"] == "io"
    assert entries[1]["subject_id"] == cam and entries[1]["result"] == "ok" and entries[1]["actor"] == "sp"


def test_district_supervisor_is_limited_to_their_district(client, cam):
    cams = surveillance.ensure(main.data_cache)["cameras"]
    by_id = {c["camera_id"]: c["district"] for c in cams}
    other = next(c["camera_id"] for c in cams if c["district"] != "Bengaluru Urban")
    home = next(c["camera_id"] for c in cams if c["district"] == "Bengaluru Urban")
    streams.configure(lambda: by_id)
    try:
        h = _token(client, "sp.bengaluru@ksp.gov.in", "scrb-sp")
        assert client.post(URL, headers=h, json={"camera_id": other, "kind": "device"}).status_code == 403
        assert client.post(URL, headers=h, json={"camera_id": home, "kind": "device"}).status_code == 200
    finally:
        streams.configure(lambda: {c["camera_id"] for c in surveillance.ensure(main.data_cache)["cameras"]})
