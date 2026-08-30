"""Put your API keys into the two gitignored config files, safely.

Run this instead of editing the files by hand. It exists because both files have a
way of going wrong that is silent:

* `web/.env.local` must be UTF-8. PowerShell's `>` redirection writes UTF-16, which
  Next.js cannot parse — the key sits in the file and the build never sees it, so the
  map quietly draws with Leaflet instead of Google and nothing says why. The current
  file is UTF-16 for exactly that reason.

  The Maps key is optional: without it the map renders on Leaflet over keyless Esri
  tiles, with every layer intact. Setting it only changes which basemap is used.
* `appsail/app-config.json` must stay valid JSON with its other values intact, so it
  is edited as JSON rather than patched as text.

Neither file is tracked by git, so nothing written here can be committed.

    python set_keys.py --maps AIza...           # map only
    python set_keys.py --groq gsk_... --nvidia nvapi-...
    python set_keys.py --maps ... --groq ... --nvidia ...

Nothing is printed back except lengths. Pass --check to see what is configured
without changing anything.
"""
import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_CONFIG = os.path.join(HERE, "appsail", "app-config.json")
ENV_LOCAL = os.path.join(HERE, "web", ".env.local")

MAPS_VAR = "NEXT_PUBLIC_GOOGLE_MAPS_API_KEY"


def _read_env_local():
    """Read .env.local whatever encoding it was written in, and return its pairs."""
    if not os.path.exists(ENV_LOCAL):
        return {}
    raw = open(ENV_LOCAL, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    pairs = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        pairs[name.strip()] = value.strip().strip('"').strip("'")
    return pairs


def _write_env_local(pairs):
    """Always UTF-8 with LF — the only form Next reads."""
    body = "".join(f"{k}={v}\n" for k, v in pairs.items() if v)
    with io.open(ENV_LOCAL, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)


def _describe(value):
    if not value:
        return "not set"
    if "REPLACE" in value:
        return "placeholder"
    return f"set ({len(value)} chars, starts {value[:6]!r})"


def check():
    print("appsail/app-config.json")
    try:
        env = json.load(io.open(APP_CONFIG, encoding="utf-8")).get("env_variables", {})
    except (OSError, ValueError) as e:
        print(f"  cannot read: {e}")
        env = {}
    for key in ("GROQ_API_KEY", "NVIDIA_API_KEY", "AUTH_SECRET", "AUDIT_HMAC_KEY"):
        print(f"  {key:16s} {_describe(env.get(key))}")

    print("\nweb/.env.local")
    if os.path.exists(ENV_LOCAL):
        raw = open(ENV_LOCAL, "rb").read()
        enc = "UTF-16 (Next cannot read this)" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "UTF-8"
        print(f"  encoding         {enc}")
        for k, v in _read_env_local().items():
            print(f"  {k:16s} {_describe(v)}" if k == MAPS_VAR
                  else f"  {k:16s} present (not used by this project)")
        if MAPS_VAR not in _read_env_local():
            print(f"  {MAPS_VAR:16s} not set")
    else:
        print("  file does not exist")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--maps", help=f"Google Maps browser key -> web/.env.local")
    ap.add_argument("--groq", help="Groq API key -> appsail/app-config.json")
    ap.add_argument("--nvidia", help="NVIDIA API key -> appsail/app-config.json")
    ap.add_argument("--check", action="store_true", help="report state, change nothing")
    args = ap.parse_args()

    if args.check or not (args.maps or args.groq or args.nvidia):
        check()
        if not (args.maps or args.groq or args.nvidia):
            print("\nNothing changed. Pass --maps / --groq / --nvidia to set a key.")
        return

    changed = []

    if args.maps:
        pairs = _read_env_local()
        # Anything already in the file is preserved: it is not this script's to delete,
        # and rewriting as UTF-8 is a repair the other variables also needed.
        pairs[MAPS_VAR] = args.maps.strip()
        _write_env_local(pairs)
        changed.append(f"web/.env.local        {MAPS_VAR} ({len(args.maps.strip())} chars, UTF-8)")

    if args.groq or args.nvidia:
        try:
            cfg = json.load(io.open(APP_CONFIG, encoding="utf-8"))
        except (OSError, ValueError) as e:
            sys.exit(f"cannot read {APP_CONFIG}: {e}")
        env = cfg.setdefault("env_variables", {})
        if args.groq:
            env["GROQ_API_KEY"] = args.groq.strip()
            changed.append(f"appsail/app-config.json  GROQ_API_KEY ({len(args.groq.strip())} chars)")
        if args.nvidia:
            env["NVIDIA_API_KEY"] = args.nvidia.strip()
            changed.append(f"appsail/app-config.json  NVIDIA_API_KEY ({len(args.nvidia.strip())} chars)")
        with io.open(APP_CONFIG, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(cfg, indent=2) + "\n")

    print("Written:")
    for line in changed:
        print("  " + line)

    print("\nNext:")
    if args.maps:
        print("  cd web && npm run build && cd .. && catalyst deploy --only client")
    if args.groq or args.nvidia:
        print("  catalyst deploy --only appsail")
    print("\nNeither file is tracked by git, so nothing here can be committed.")


if __name__ == "__main__":
    main()
