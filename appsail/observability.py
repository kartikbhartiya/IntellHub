"""Structured logging, request correlation, and honest error responses.

What this replaces
------------------
The application had no `logging` import at all. Forty-seven `print()` calls went to
gunicorn's stdout with no timestamp, no level, no request correlation and no way to
filter. There was also no error handler, so an unhandled exception returned
Werkzeug's HTML page — from an API whose frontend parses JSON everywhere, which
turns a 500 into a confusing parse error two layers away from the actual fault.

The failure this is really aimed at
-----------------------------------
A total ML-layer failure used to return HTTP 200 on every route while `/health`
reported `"status": "online"`. Every model could be unavailable and nothing —
not a status code, not a log line an operator would notice, not a health check —
would say so. The dashboard simply rendered empty charts.

So `health_report()` inspects what actually loaded rather than whether a dataframe
key exists, and returns a degraded verdict that `/health` surfaces as a non-200.
A monitor pointed at `/health` now trips instead of going quiet.
"""
import json
import logging
import os
import sys
import time
import uuid

from flask import g, jsonify, request

LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# Human-readable lines locally; one JSON object per line in a container, where
# something else has to parse them.
JSON_LOGS = os.environ.get("LOG_FORMAT", "").lower() == "json" or bool(
    os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT"))

LOG = logging.getLogger("ksp")


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None)
        if rid:
            payload["request_id"] = rid
        for key in ("route", "method", "status", "ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _RequestIdFilter(logging.Filter):
    """Attach the current request's id to every record emitted during it."""

    def filter(self, record):
        try:
            record.request_id = getattr(g, "request_id", None)
        except RuntimeError:
            record.request_id = None
        return True


def configure():
    if LOG.handlers:
        return LOG
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonFormatter() if JSON_LOGS
        else logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    handler.addFilter(_RequestIdFilter())
    LOG.addHandler(handler)
    LOG.setLevel(getattr(logging, LEVEL, logging.INFO))
    LOG.propagate = False
    return LOG


def install(app, data_cache):
    """Wire request correlation, latency and JSON error handling into the app."""
    configure()

    @app.before_request
    def _start_timer():
        g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        g.started_at = time.perf_counter()

    @app.after_request
    def _log_request(response):
        try:
            elapsed = (time.perf_counter() - getattr(g, "started_at", time.perf_counter())) * 1000
            response.headers["X-Request-Id"] = getattr(g, "request_id", "")
            # Slow requests are the ones worth reading later; everything else is
            # noise at INFO and available at DEBUG.
            level = logging.WARNING if elapsed > 2000 else logging.INFO
            if response.status_code >= 500:
                level = logging.ERROR
            LOG.log(level, "%s %s -> %s", request.method, request.path,
                    response.status_code,
                    extra={"route": request.path, "method": request.method,
                           "status": response.status_code, "ms": round(elapsed, 1)})
        except Exception:
            pass
        return response

    @app.errorhandler(404)
    def _not_found(_e):
        return jsonify({"error": "not_found", "path": request.path,
                        "request_id": getattr(g, "request_id", None)}), 404

    @app.errorhandler(405)
    def _not_allowed(_e):
        return jsonify({"error": "method_not_allowed", "path": request.path,
                        "method": request.method,
                        "request_id": getattr(g, "request_id", None)}), 405

    @app.errorhandler(Exception)
    def _unhandled(e):
        # Re-raise Flask's own HTTP errors so their status codes survive.
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return jsonify({"error": e.name.lower().replace(" ", "_"),
                            "status": e.code,
                            "request_id": getattr(g, "request_id", None)}), e.code
        rid = getattr(g, "request_id", None)
        LOG.exception("unhandled error on %s", request.path, extra={"route": request.path})
        # The message is deliberately not echoed to the caller: an exception string
        # from a system holding case records can contain fragments of them. The
        # request id is the handle for correlating with the log.
        return jsonify({
            "error": "internal_error",
            "request_id": rid,
            "hint": "Quote this request_id when reporting the failure.",
        }), 500

    return app


# ---------------------------------------------------------------- health
def health_report(data_cache, knowledge_graph=None):
    """What is actually working, not merely whether a dict key exists.

    Returns (payload, http_status). The status is 503 when the platform is serving
    but degraded, so a monitor trips instead of reading 200 off a broken system.
    """
    loaded = "firs" in data_cache
    sources = data_cache.get("ml_sources", {}) or {}
    failed = sorted(k for k, v in sources.items() if v in ("failed", "unavailable"))
    retrained = sorted(k for k, v in sources.items() if v == "retrained")

    degraded = []
    if not loaded:
        degraded.append("corpus not loaded")
    if sources and failed:
        degraded.append(f"{len(failed)} model(s) unavailable: {', '.join(failed)}")
    if loaded and not sources:
        degraded.append("ML layer never reported a load result")
    # Only meaningful once the corpus is up; during the ~20s cold load `firs` can be
    # present while dispatch is still being computed, and flagging that would take a
    # healthy container out of a load-balancer pool while it is starting.
    if loaded and data_cache.get("_load_complete") and not data_cache.get("dispatch"):
        degraded.append("dispatch recommendations unavailable")
    # Deliberately NOT degrading on an empty anomaly list. IsolationForest finding
    # nothing is a legitimate result, and treating it as a fault pinned /health at
    # 503 permanently — which trains an operator to ignore the signal. Only an
    # anomaly *model* that failed to build is a degradation, and that is already
    # covered by the ml_sources check above.

    payload = {
        "status": "online" if not degraded else "degraded",
        "degraded_reasons": degraded,
        "data_loaded": loaded,
        "models": {
            "expected": len(sources),
            "from_artifact": sorted(k for k, v in sources.items() if v == "artifact"),
            "retrained_in_process": retrained,
            "unavailable": failed,
        },
    }
    if loaded:
        firs = data_cache["firs"]
        payload.update({
            "total_cases": int(len(firs)),
            "total_suspects": int(len(data_cache.get("suspects", []))),
            "anomalies_flagged": len(data_cache.get("anomalies", [])),
            "trend_alerts": len(data_cache.get("trend_alerts", [])),
        })
    if knowledge_graph is not None:
        payload["graph_nodes"] = knowledge_graph.number_of_nodes()
        payload["graph_edges"] = knowledge_graph.number_of_edges()

    return payload, (200 if not degraded else 503)
