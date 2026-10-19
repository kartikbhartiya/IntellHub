"""Live video sources attached to cameras.

Why this exists
---------------
The CCTV wall plays recorded Bengaluru street footage (web/src/lib/footage.ts) because
the KSP Safe City and traffic-police feeds are not public. That is honest only while it
is labelled RECORDED, and it leaves no way to show a real live picture when one exists.
This module is that way: an administrator attaches a live stream to a camera id - the
HLS output of an NVR or camera gateway, a WebRTC (WHEP) endpoint on a media server, an
MJPEG camera, a public YouTube Live broadcast of a Karnataka public place, or "the
viewing officer's own device camera" for a demonstration - and every officer's wall
plays it.

What it deliberately does not do
--------------------------------
* **It never fetches, probes or relays a stream.** The URL is handed to the viewer's
  browser, which plays it directly. So this server cannot vouch that a source is live,
  only that its URL is well-formed and safe to hand out. Whether a tile says LIVE is
  decided in the browser, from frames actually arriving (CameraFeed.tsx).
* **It never invents a source.** With nothing configured every camera stays on its
  recorded clip. The `video_url` column of cctv.csv is part of the simulated corpus and
  points at a host that does not serve video; it is not used here.
* **It runs no analytics.** The YOLO boxes exist only for the recorded clips they were
  computed on; a live tile says so.

Rules a URL must meet
---------------------
* ``https://`` - or ``http://`` only to a LAN host (RFC 1918, loopback, link-local,
  ``localhost``, ``*.local``, a single-label intranet name), so an on-prem NVR works
  without a certificate while nothing crosses the internet in cleartext. A browser on
  an https page still blocks http media as mixed content; that path is for an
  on-prem deployment served over http on the same LAN.
* **No credentials in the URL.** The map is served to every investigator, so a
  password in a URL is a password handed to all of them. Userinfo (``user:pass@``) and
  credential-named query parameters are refused. Expiring signed URLs (``token=`` with
  ``expires=``) are allowed: they grant viewing of one stream, not the camera's account.
* ``rtsp://`` gets a specific answer - browsers cannot play it; it needs a gateway
  (MediaMTX, go2rtc) that re-serves it as HLS or WHEP.
* YouTube links are reduced to their 11-character video id, so what is stored is an
  embeddable id rather than whatever page the link happened to point at.

Who may do what
---------------
Reading the map needs investigator, because a live stream can show people. Changing
it needs supervisor and is audited, with the camera id as the subject; the file also
keeps the last changes (who attached which URL, when) since the audit line itself does
not carry the URL. When the camera getter supplies districts, a district-scoped
supervisor may change only cameras in their district - the same rule main.py applies
to acting on another district's alert.

Storage
-------
JSON at ``CAMERA_STREAMS_PATH`` (default ``appsail/camera_streams.json``), replaced
atomically (temp file in the same directory, fsync, ``os.replace``). ``CAMERA_STREAMS``
in the environment (JSON) supplies read-only defaults for a deployment - on Catalyst
AppSail the disk is ephemeral, so that variable is the durable place for permanent
sources, and the file only overrides it until the next restart.
"""
import copy
import ipaddress
import unicodedata
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import parse_qs, parse_qsl, urlsplit

from flask import Blueprint, jsonify, request

import audit
import auth

LOG = logging.getLogger("ksp.streams")

bp = Blueprint("camera_streams", __name__)

ROUTE = "/serverless/api/surveillance/streams"

_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_URL = 2048
MAX_LABEL = 120
MAX_ATTRIBUTION = 200
MAX_CAMERA_ID = 64
HISTORY_KEPT = 200

# Published by GET so the UI renders the choices the server validates, not a copy.
KINDS = [
    {"kind": "hls", "label": "HLS (.m3u8)", "needs_url": True,
     "hint": "An HLS playlist from an NVR, camera gateway or media server (MediaMTX, go2rtc, "
             "Wowza, nginx-rtmp). The server must send CORS headers for this site."},
    {"kind": "webrtc-whep", "label": "WebRTC (WHEP)", "needs_url": True,
     "hint": "A WHEP endpoint, e.g. MediaMTX https://host:8889/<path>/whep. Sub-second "
             "latency; the media server must be reachable from the viewer's network."},
    {"kind": "mjpeg", "label": "MJPEG", "needs_url": True,
     "hint": "A multipart MJPEG stream from an IP camera or gateway - not a snapshot .jpg, "
             "which is a still picture."},
    {"kind": "mp4", "label": "Live MP4", "needs_url": True,
     "hint": "A live fragmented-MP4 stream (go2rtc: /api/stream.mp4?src=<camera>). A finished "
             "file is a recording and the wall will badge it as one."},
    {"kind": "youtube", "label": "YouTube Live", "needs_url": True,
     "hint": "A youtube.com/watch?v=..., youtu.be/... or youtube.com/live/... link to a public "
             "broadcast that allows embedding. Credit the channel in Attribution."},
    {"kind": "device", "label": "This device's camera", "needs_url": False,
     "hint": "Plays the viewing officer's own camera after they click Start - for a live "
             "demonstration. It is not the CCTV camera at this site, and the tile says so."},
]
KIND_IDS = [k["kind"] for k in KINDS]

# Query parameters whose name says they carry an account secret. `token`, `sig`,
# `signature` and `expires` are left alone on purpose: that is the shape of an
# expiring signed URL, which is how CDNs and media servers grant viewing of one stream.
_CREDENTIAL_PARAMS = {
    "password", "passwd", "pwd", "pass", "passphrase", "secret", "client_secret",
    "user", "username", "usr", "login", "auth", "authorization", "basic",
    "apikey", "api_key", "access_key", "access_token", "private_key", "privatekey",
}

# Networks that never route across the public internet. Listed rather than taken from
# ipaddress.is_private, whose meaning has shifted between Python versions (3.13 changed
# it for several ranges); a security rule should not move with the interpreter.
_LAN_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",   # RFC 1918
    "127.0.0.0/8", "169.254.0.0/16",                    # loopback, link-local (camera auto-IP)
    "::1/128", "fc00::/7", "fe80::/10",                 # IPv6 loopback, ULA, link-local
)]
_LAN_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost")

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
             "youtube-nocookie.com", "www.youtube-nocookie.com"}

_lock = threading.RLock()
_file_cache = {"sig": None, "doc": None, "error": None}
_env_cache = {"raw": None, "entries": {}, "problems": []}

_camera_ids = None


def configure(camera_ids_getter):
    """Bind the camera registry. `camera_ids_getter()` returns the known camera ids.

    A set is enough. If it returns a mapping of camera id -> district instead, a
    district-scoped supervisor is also limited to their own district's cameras. It is
    called on every request, never cached, because the corpus can be reloaded and
    the camera network rebuilt under it.
    """
    global _camera_ids
    _camera_ids = camera_ids_getter


def _known():
    if _camera_ids is None:
        raise RuntimeError("streams.configure(camera_ids_getter) was never called")
    return _camera_ids()


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _path():
    # Read per call, not at import: the test suite, and a deployment that mounts a
    # persistent volume, point this somewhere else after the module is imported.
    return os.environ.get("CAMERA_STREAMS_PATH") or os.path.join(_DIR, "camera_streams.json")


# =============================================================================
# Validation
# =============================================================================
class Invalid(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def _clean_text(value, field, limit):
    """An optional single line of display text."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise Invalid(field, f"{field.capitalize()} must be text.")
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        raise Invalid(field, f"{field.capitalize()} is longer than {limit} characters.")
    # Every Unicode "other" category: controls, format characters such as bidi
    # overrides (which can visually reverse an attribution), and line separators.
    if any(unicodedata.category(c)[0] in ("C", "Z") and c != " " for c in text):
        raise Invalid(field, f"{field.capitalize()} contains control characters.")
    return text


def is_lan_host(host):
    """True when `host` can only be reached on a private network."""
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    # Browsers percent-decode, IDNA-map and accept numeric IPv4 forms in a host, so
    # "evil%2ecom", "evil\u3002com" and "2130706433" are all dotless but public. Only
    # plain ASCII hostnames and bracketed IPv6 literals are judged at all.
    if not re.fullmatch(r"[a-z0-9.\-]+|\[?[0-9a-f:.]+\]?", h):
        return False
    if h == "localhost" or h.endswith(_LAN_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        # A single-label name ("nvr-blr-03") resolves only through the LAN's own DNS
        # search domain; nothing on the public internet is served from one.
        return ("." not in h and ":" not in h
                and re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", h) is not None
                and re.fullmatch(r"0x[0-9a-f]*|[0-9]+", h) is None)
    return any(ip in net for net in _LAN_NETS)


def check_url(raw):
    """A URL that is safe to hand to every viewer's browser, or Invalid."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise Invalid("url", "A URL is required for this kind of source.")
    if not isinstance(raw, str):
        raise Invalid("url", "The URL must be text.")
    url = raw.strip()
    if len(url) > MAX_URL:
        raise Invalid("url", f"The URL is longer than {MAX_URL} characters.")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise Invalid("url", "The URL contains spaces or control characters.")
    try:
        parts = urlsplit(url)
        parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise Invalid("url", "That is not a valid URL.")
    scheme = parts.scheme.lower()
    if scheme in ("rtsp", "rtsps", "rtmp", "rtmps", "srt", "udp", "rtp"):
        raise Invalid("url", f"Browsers cannot play {scheme}:// directly. Put the camera behind a "
                             "gateway (MediaMTX or go2rtc) that re-serves it as HLS or WebRTC "
                             "(WHEP), and attach that URL.")
    if scheme not in ("https", "http"):
        raise Invalid("url", "Only https:// URLs can be attached (or http:// to a LAN host).")
    # Checked on the raw netloc rather than parts.username: "https://:secret@host" has
    # an empty username and would slip past a truthiness test.
    if "@" in parts.netloc:
        raise Invalid("url", "Remove the user name and password from the URL. Every investigator "
                             "receives this URL, so credentials in it are shared with all of them; "
                             "use a gateway that serves the stream without them.")
    host = parts.hostname
    if not host:
        raise Invalid("url", "The URL has no host.")
    if scheme == "http" and not is_lan_host(host):
        raise Invalid("url", "Plain http:// is accepted only for a LAN host (10.x, 172.16-31.x, "
                             "192.168.x, localhost, *.local). Use https:// for anything reached "
                             "over the internet.")
    for key, _value in parse_qsl(parts.query, keep_blank_values=True):
        if key.strip().lower().replace("-", "_") in _CREDENTIAL_PARAMS:
            raise Invalid("url", f"The URL carries a credential ('{key}='). Every investigator "
                                 "receives this URL; serve the stream without account secrets "
                                 "in it (an expiring signed token is fine).")
    return url


def youtube_id(url):
    """The 11-character video id of a YouTube link, or None.

    Accepts watch?v=, youtu.be/, /live/, /embed/ and /shorts/ forms on youtube.com,
    m.youtube.com and youtube-nocookie.com. A channel's /live page or an @handle is not
    accepted: resolving it to a video would mean fetching YouTube, and this server
    does not fetch stream sources.
    """
    try:
        parts = urlsplit(url.strip())
    except (AttributeError, ValueError):
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("https", "http") or host not in _YT_HOSTS:
        return None
    segs = [s for s in parts.path.split("/") if s]
    cand = None
    if host == "youtu.be":
        cand = segs[0] if segs else None
    elif segs[:1] == ["watch"]:
        cand = (parse_qs(parts.query).get("v") or [None])[0]
    elif len(segs) >= 2 and segs[0] in ("live", "embed", "shorts", "v"):
        cand = segs[1]
    return cand if cand and _YT_ID.match(cand) else None


def _canonical_camera(camera_id, known):
    if not isinstance(camera_id, str) or not camera_id.strip():
        raise Invalid("camera_id", "camera_id is required.")
    cid = camera_id.strip()
    if len(cid) > MAX_CAMERA_ID:
        raise Invalid("camera_id", "camera_id is too long.")
    for c in (cid, cid.upper()):
        if c in known:
            return c
    raise Invalid("camera_id", f"No camera {cid} in the network.")


def validate(entry):
    """The stored record for one source (without camera id or provenance), or Invalid.

    Used for operator POSTs, for the deployment's CAMERA_STREAMS and for records read
    back from the file, so a hand-edited file cannot slip past the rules a POST meets.
    """
    if not isinstance(entry, Mapping):
        raise Invalid("body", "A stream must be a JSON object.")
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind.strip().lower() not in KIND_IDS:
        raise Invalid("kind", "Kind must be one of: " + ", ".join(KIND_IDS) + ".")
    kind = kind.strip().lower()
    rec = {"kind": kind, "url": None, "video_id": None,
           "label": _clean_text(entry.get("label"), "label", MAX_LABEL),
           "attribution": _clean_text(entry.get("attribution"), "attribution", MAX_ATTRIBUTION)}
    raw_url = entry.get("url")
    if kind == "device":
        if isinstance(raw_url, str) and raw_url.strip():
            raise Invalid("url", "A device source plays the viewer's own camera and takes no URL.")
        return rec
    url = check_url(raw_url)
    if kind == "youtube":
        vid = youtube_id(url)
        if not vid:
            raise Invalid("url", "Not a YouTube video link. Paste the broadcast's "
                                 "youtube.com/watch?v=..., youtu.be/... or youtube.com/live/... "
                                 "URL (a channel's /live page cannot be resolved here).")
        rec["video_id"] = vid
        rec["url"] = f"https://www.youtube.com/watch?v={vid}"
        return rec
    if youtube_id(url):
        raise Invalid("kind", "That is a YouTube link - choose the YouTube Live kind.")
    rec["url"] = url
    return rec


# =============================================================================
# Storage
# =============================================================================
def _empty_doc():
    return {"version": 1, "streams": {}, "history": []}


def _read_file():
    """The operator document, re-read only when the file changed on disk."""
    path = _path()
    with _lock:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            _file_cache.update(sig=None, doc=_empty_doc(), error=None)
            return _file_cache["doc"]
        except OSError as e:
            _file_cache.update(sig=None, doc=_empty_doc(), error=f"unreadable: {e}")
            return _file_cache["doc"]
        sig = (path, st.st_mtime_ns, st.st_size)
        if _file_cache["sig"] == sig and _file_cache["doc"] is not None:
            return _file_cache["doc"]
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            if not isinstance(doc, dict) or not isinstance(doc.get("streams"), dict):
                raise ValueError("not a camera-streams document")
            doc.setdefault("history", [])
            _file_cache.update(sig=sig, doc=doc, error=None)
        except (OSError, ValueError) as e:
            # Serve nothing from a damaged file rather than a guess at its contents,
            # and say so; _write() moves it aside instead of overwriting it.
            LOG.error("camera streams file %s is unreadable: %s", path, e)
            _file_cache.update(sig=sig, doc=_empty_doc(), error=f"unreadable: {e}")
        return _file_cache["doc"]


def _write(doc):
    path = _path()
    folder = os.path.dirname(path) or "."
    with _lock:
        os.makedirs(folder, exist_ok=True)
        if _file_cache.get("error") and os.path.exists(path):
            # Never overwrite a file we could not parse: it may hold sources someone
            # needs back. Keep it beside the new one.
            aside = f"{path}.unreadable-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            os.replace(path, aside)
            LOG.error("moved unreadable camera streams file to %s", aside)
        fd, tmp = tempfile.mkstemp(prefix=".camera_streams.", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        st = os.stat(path)
        _file_cache.update(sig=(path, st.st_mtime_ns, st.st_size), doc=doc, error=None)


def _env_defaults():
    """CAMERA_STREAMS, parsed once per distinct value: ({camera_id: entry}, problems)."""
    raw = os.environ.get("CAMERA_STREAMS") or ""
    with _lock:
        if _env_cache["raw"] == raw:
            return _env_cache["entries"], _env_cache["problems"]
        entries, problems = {}, []
        if raw.strip():
            try:
                data = json.loads(raw)
            except ValueError as e:
                data = None
                problems.append({"camera_id": None, "origin": "deployment",
                                 "reason": f"CAMERA_STREAMS is not valid JSON ({e.msg})."})
            # Either {"CAM-..": {...}} or [{"camera_id": "CAM-..", ...}].
            if isinstance(data, dict):
                items = [dict(v, camera_id=k) if isinstance(v, Mapping) else {"camera_id": k, "bad": v}
                         for k, v in data.items()]
            elif isinstance(data, list):
                items = list(data)
            else:
                items = []
                if data is not None:
                    problems.append({"camera_id": None, "origin": "deployment",
                                     "reason": "CAMERA_STREAMS must be a JSON object or list."})
            for item in items:
                cid = item.get("camera_id") if isinstance(item, Mapping) else None
                if not isinstance(cid, str) or not cid.strip():
                    problems.append({"camera_id": None, "origin": "deployment",
                                     "reason": "An entry has no camera_id."})
                    continue
                entries[cid.strip()] = item
        _env_cache.update(raw=raw, entries=entries, problems=problems)
        return entries, problems


# =============================================================================
# The map served to the wall
# =============================================================================
def snapshot(include_admin=False):
    """Every valid source, with provenance. Invalid or orphaned entries are reported,
    not served: a camera id that no longer exists (a reloaded corpus) or an entry
    that breaks the rules plays nothing rather than something unchecked."""
    known = _known()
    defaults, problems = _env_defaults()
    doc = _read_file()
    streams, ignored = {}, list(problems)

    for cid, entry in defaults.items():
        try:
            canon = _canonical_camera(cid, known)
            rec = validate(entry)
        except Invalid as e:
            ignored.append({"camera_id": cid, "origin": "deployment", "reason": e.message})
            continue
        rec.update(camera_id=canon, origin="deployment", overrides_default=False,
                   added_by=entry.get("added_by") if isinstance(entry.get("added_by"), str) else None,
                   added_at=None)
        streams[canon] = rec

    for cid, stored in doc["streams"].items():
        try:
            canon = _canonical_camera(cid, known)
            rec = validate(stored)
        except Invalid as e:
            ignored.append({"camera_id": cid, "origin": "operator", "reason": e.message})
            continue
        rec.update(camera_id=canon, origin="operator", overrides_default=canon in streams,
                   added_by=stored.get("added_by"), added_at=stored.get("added_at"))
        streams[canon] = rec

    out = {
        "streams": streams,
        "kinds": KINDS,
        "count": len(streams),
        "can_edit": auth.has_role("supervisor"),
        "note": ("Each source is a URL handed to the viewer's browser; this server does not "
                 "fetch, check or relay it. A tile is badged LIVE only while the browser is "
                 "receiving frames, and no analytics run on a live picture."),
    }
    if include_admin:
        out["storage"] = {
            "file": os.path.basename(_path()),
            "file_error": _file_cache.get("error"),
            "deployment_defaults": len(defaults),
            "note": ("Operator changes are saved to this server's disk. On a platform whose disk "
                     "is ephemeral (Catalyst AppSail) they last until the next restart; put "
                     "permanent sources in the CAMERA_STREAMS environment variable."),
        }
        out["ignored"] = ignored
        out["history"] = list(reversed(doc.get("history", [])[-25:]))
    return out


def _bad(status, error, field, message):
    # A (body, status) tuple, not a Response with a status: audit.audited reads the
    # status from the tuple, so a refused change is logged as denied rather than ok.
    return jsonify({"error": error, "errors": {field: message}, "message": message}), status


def _camera_from_body(fn):
    """Lift `camera_id` from the JSON body into the view's kwargs.

    audit.audited names its subject from a view argument; the POST carries the camera
    in the body, and an audit line that says only "set_camera_stream" is useless six
    months later. This sits above @audit.audited so the subject is present when the
    entry is written.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method == "POST":
            body = request.get_json(silent=True)
            cid = body.get("camera_id") if isinstance(body, dict) else None
            kwargs["camera_id"] = cid.strip()[:MAX_CAMERA_ID] if isinstance(cid, str) else None
        return fn(*args, **kwargs)
    return wrapper


# =============================================================================
# Routes
# =============================================================================
@bp.route(ROUTE, methods=["GET"])
@audit.audited("read_camera_streams")
@auth.require_role("investigator")
def route_streams_list():
    return jsonify(snapshot(include_admin=auth.has_role("supervisor")))


@bp.route(ROUTE, methods=["POST", "OPTIONS"])
@_camera_from_body
@audit.audited("set_camera_stream", subject_type="camera", subject_arg="camera_id")
@auth.require_role("supervisor")
def route_streams_set(camera_id=None):
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _bad(400, "invalid_stream", "body", "Send a JSON object.")
    known = _known()
    try:
        cid = _canonical_camera(camera_id, known)
    except Invalid as e:
        return _bad(404 if camera_id else 400, "unknown_camera" if camera_id else "invalid_stream",
                    e.field, e.message)

    ident = auth.current_identity()
    scope = ident.get("scope") or {}
    if isinstance(known, Mapping) and scope.get("level", "state") != "state":
        if str(known.get(cid)) != str(scope.get("district")):
            return jsonify({
                "error": "outside_jurisdiction", "camera_district": known.get(cid),
                "scope": dict(scope), "errors": {"camera_id": "This camera is outside your district."},
                "message": "A camera's live source is set by the officers of its district.",
            }), 403

    actor = ident.get("user") or "anonymous"
    remove = data.get("remove", False)
    if remove not in (True, False, None):
        return _bad(400, "invalid_stream", "remove", "remove must be true or absent.")

    with _lock:
        # A copy: the cached document must not change unless the write lands.
        doc = copy.deepcopy(_read_file())
        if remove is True:
            removed = doc["streams"].pop(cid, None) is not None
            if not removed:
                defaults, _ = _env_defaults()
                if any(k == cid or k.upper() == cid for k in defaults):
                    return _bad(409, "deployment_default", "camera_id",
                                "This camera's source is set by the deployment (CAMERA_STREAMS) and "
                                "cannot be removed here; change it in the environment.")
                return jsonify(dict(snapshot(include_admin=True), ok=True, camera_id=cid, removed=False))
            doc["history"].append({"at": _now_iso(), "by": actor, "camera_id": cid, "action": "remove"})
        else:
            try:
                rec = validate(data)
            except Invalid as e:
                return _bad(400, "invalid_stream", e.field, e.message)
            if rec["video_id"] is None:
                del rec["video_id"]
            rec.update(added_by=actor, added_at=_now_iso())
            doc["streams"][cid] = rec
            doc["history"].append({"at": rec["added_at"], "by": actor, "camera_id": cid,
                                   "action": "set", "kind": rec["kind"], "url": rec["url"]})
        doc["history"] = doc["history"][-HISTORY_KEPT:]
        try:
            _write(doc)
        except OSError as e:
            LOG.error("camera streams not saved: %s", e)
            return _bad(503, "storage_unavailable", "body",
                        "The server could not save the change to disk. Set CAMERA_STREAMS_PATH to a "
                        "writable location, or configure the source in CAMERA_STREAMS.")
    return jsonify(dict(snapshot(include_admin=True), ok=True, camera_id=cid,
                        removed=bool(remove)))
