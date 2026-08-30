"""AppSail entrypoint.

Do **not** import `main` here.

`main` starts a background warm-up thread at import time (see the
`threading.Thread(target=_warmup...)` at the bottom of main.py) which builds the
full corpus, knowledge graph and models. So `from main import app` in this
parent process does not merely check that the app is importable — it loads
everything. `os.system` then starts gunicorn, which imports `main` a second time
and loads everything again, concurrently.

At 5,000 cases that was roughly 150 MB twice and fit inside the container. At
18,000 cases it is roughly 370 MB twice, the container is OOM-killed before
gunicorn can bind, and AppSail reports:

    Execution failed. Please check the startup command or port.

which says nothing about memory and sends you looking at the startup command.

So: no pre-import. This process holds nothing but the standard library, gunicorn
loads the app exactly once, and if that load fails the traceback is served rather
than left as an opaque 503.

One worker, threaded. The dataset, graph and fitted models live in module-level
state, so a second worker would be a second full copy. Concurrency comes from
threads sharing the one copy.
"""
import os
import sys

port = int(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT", os.environ.get("PORT", "9000")))

cmd = (
    # 180s, not 0. The first request triggers a ~20s corpus load, which is why
    # the timeout was disabled — but disabling it means a hung request holds a
    # thread forever, and with 8 threads, 8 such requests wedge the service
    # permanently with no automatic recovery. 180s clears the cold load with a
    # wide margin and still lets the worker be reclaimed.
    f"gunicorn --bind 0.0.0.0:{port} --workers 1 --threads 8 "
    f"--timeout 180 --graceful-timeout 30 "
    f"--access-logfile - --error-logfile - wsgi:app"
)
print(f"exec: {cmd}", flush=True)
rc = os.system(cmd)

if rc != 0:
    # gunicorn could not start or the app failed to import under it. Bind the
    # port ourselves and serve the reason, so the failure is diagnosable from a
    # browser instead of being an opaque gateway 503.
    print(f"gunicorn exited with {rc}; serving diagnostic page", file=sys.stderr, flush=True)
    import traceback

    from flask import Flask

    detail = f"gunicorn exited with status {rc}\n\n"
    try:
        import main  # noqa: F401
        detail += "main imported cleanly when retried directly.\n"
    except Exception:
        detail += traceback.format_exc()

    fallback = Flask(__name__)

    @fallback.route("/", defaults={"path": ""})
    @fallback.route("/<path:path>")
    def catch_all(path):
        return f"App failed to start.\n\n{detail}", 500

    fallback.run(host="0.0.0.0", port=port)
