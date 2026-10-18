"""Build the copilot's knowledge of the platform from the repository itself.

Why this exists
---------------
`copilot_knowledge.TOPICS` is hand-written. It was accurate the day it was written
and starts drifting the first time someone adds a page, a route or a model without
remembering to update it — which is every time. The copilot then describes a
platform that no longer exists, confidently.

This script reads what the repository actually contains and writes it to
`artifacts/project_knowledge.json`:

* every Markdown document at the repo root, split by heading;
* the module docstring of every backend module (`appsail/*.py`, `appsail/ml/**`);
* every API route in `main.py` — path, methods, the role it requires, its docstring;
* every page of the web client — its URL and the headings and copy it shows.

The artifact is committed because AppSail deploys `appsail/` alone, so the docs and
the web client are not on disk in production. In a full checkout the copilot
rebuilds it in memory whenever a source file is newer than the artifact, and CI runs
`python build_project_knowledge.py --check`, which fails when the committed copy is
stale. Change the project, run this once, commit — and the copilot knows.

Usage:
    python build_project_knowledge.py          # rebuild the artifact
    python build_project_knowledge.py --check  # exit 1 if it is out of date
"""
import ast
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ARTIFACT = os.path.join(HERE, "artifacts", "project_knowledge.json")
MAX_CHUNK = 1400
SKIP_DIRS = {"_vendor", "__pycache__", "node_modules", ".next", "out", "tests", "artifacts", "data"}


def _chunks(text, limit=MAX_CHUNK):
    """Split long text on paragraph boundaries into pieces of at most `limit`."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(cur) + len(para) + 2 > limit and cur:
            out.append(cur.strip())
            cur = ""
        while len(para) > limit:
            out.append(para[:limit])
            para = para[limit:]
        cur += para + "\n\n"
    if cur.strip():
        out.append(cur.strip())
    return out


def _clean_md(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)          # code blocks carry no prose
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def markdown_docs():
    out = []
    for name in sorted(os.listdir(ROOT)):
        if not name.lower().endswith(".md"):
            continue
        with open(os.path.join(ROOT, name), encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        doc_title = name[:-3]
        sections = re.split(r"(?m)^(#{1,4})\s+(.+)$", raw)
        # re.split with two groups yields [pre, hashes, title, body, hashes, title, body, ...]
        pre = _clean_md(sections[0])
        if pre:
            for c in _chunks(pre):
                out.append({"source": name, "kind": "doc", "title": doc_title, "text": c})
        trail = []
        for i in range(1, len(sections) - 2, 3):
            level, title, body = len(sections[i]), sections[i + 1].strip(), sections[i + 2]
            title = re.sub(r"[`*_]", "", title)
            trail = trail[:level - 1] + [title]
            body = _clean_md(body)
            if not body:
                continue
            for c in _chunks(body):
                out.append({"source": name, "kind": "doc", "title": " › ".join(trail), "text": c})
    return out


def module_docstrings():
    out = []
    for base, dirs, files in os.walk(HERE):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if not f.endswith(".py") or f.startswith("test_"):
                continue
            path = os.path.join(base, f)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = ast.get_docstring(ast.parse(fh.read()))
            except (SyntaxError, ValueError, OSError):
                continue
            if doc and len(doc) > 60:
                for c in _chunks(doc):
                    out.append({"source": rel, "kind": "module", "title": f"Backend module {rel}", "text": c})
    return out


# Modules that define routes on a Flask Blueprint (`bp.route(f"{API}/...")`).
BLUEPRINT_MODULES = ("fir_registry.py", "insights.py", "network_intel.py", "news.py", "streams.py",
                     "temporal.py", "trend_forecast.py")


def _route_path(node):
    """A route path from a string constant or an f-string over the module's API prefix."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(v.value)
            elif isinstance(v, ast.FormattedValue) and ast.unparse(v.value) == "API":
                out.append("/serverless/api")
            else:
                return None
        return "".join(out)
    return None


def api_routes():
    trees = []
    for name in ("main.py",) + BLUEPRINT_MODULES:
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                trees.append((name, ast.parse(fh.read())))
    routes = []
    for source, tree in trees:
        routes.extend(_routes_in(source, tree))
    return _route_chunks(routes)


def _routes_in(source, tree):
    routes = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        paths, methods, role, action = [], ["GET"], "public", None
        for d in node.decorator_list:
            if not isinstance(d, ast.Call):
                continue
            fn = ast.unparse(d.func)
            args = [a.value for a in d.args if isinstance(a, ast.Constant)]
            if (fn.endswith("app.route") or fn.endswith("bp.route")) and d.args:
                p = _route_path(d.args[0])
                if p:
                    paths.append(p)
                for kw in d.keywords:
                    if kw.arg == "methods":
                        methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
            elif fn.endswith("require_role") and args:
                role = args[0]
            elif fn.endswith("require_permission") and args:
                # Permission gates do not map onto one rung of the ladder, so the
                # index names the permission and the roles that hold it.
                import auth as _auth
                holders = [r for r in _auth.ROLES if args[0] in _auth.permissions_for(r)]
                role = f"{args[0]} ({', '.join(holders)})"
            elif fn.endswith("audited") and args:
                action = args[0]
        for p in paths:
            routes.append({"path": p, "methods": [m for m in methods if m != "OPTIONS"], "role": role,
                           "audited_as": action, "handler": node.name, "source": source,
                           "doc": (ast.get_docstring(node) or "").split("\n\n")[0][:500]})
    return routes


def _route_chunks(routes):
    routes.sort(key=lambda r: r["path"])
    chunks = []
    for r in routes:
        if not r["path"].startswith("/serverless/api"):
            continue
        words = " ".join(r["path"].strip("/").split("/")[2:]).replace("<", "").replace(">", "").replace("-", " ")
        chunks.append({"source": f"appsail/{r['source']}", "kind": "route", "title": f"API route {r['path']}",
                       "text": f"{r['path']} ({', '.join(r['methods'])}) — access: {r['role']}"
                               + (f"; audited as {r['audited_as']}" if r["audited_as"] else "")
                               + f"; handler {r['handler']} ({words})" + (f". {r['doc']}" if r["doc"] else "")})
    return routes, chunks


_ERROR_STATE = re.compile(r"unavailable|not found|no matching|not specified|failed|error", re.I)


def web_pages():
    app_dir = os.path.join(ROOT, "web", "src", "app")
    pages = []
    if not os.path.isdir(app_dir):
        return pages, []
    for base, dirs, files in os.walk(app_dir):
        dirs[:] = sorted(dirs)
        if "page.tsx" not in files:
            continue
        rel = os.path.relpath(base, app_dir).replace(os.sep, "/")
        url = "/" if rel == "." else "/" + rel
        with open(os.path.join(base, "page.tsx"), encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        if "client-redirect" in src and len(src) < 1500:
            continue                                   # static-export shims, not real screens
        redirect = re.search(r"[`'\"](/[a-z][^`'\"$&]*)", src) if "router.replace" in src else None
        if redirect and len(src) < 2000:
            pages.append({"url": url, "file": os.path.relpath(os.path.join(base, "page.tsx"), ROOT).replace(os.sep, "/"),
                          "headings": [], "labels": [], "copy": [],
                          "description": f"Alias that redirects to {redirect.group(1)}"})
            continue
        headings = []
        for inner in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", src, re.S):
            txt = re.sub(r"\{[^{}]*\}", " ", re.sub(r"<[^>]*>", " ", inner))
            txt = re.sub(r"\s+", " ", txt).strip()
            if 3 <= len(txt) <= 120 and "{" not in txt and "}" not in txt and not _ERROR_STATE.search(txt):
                headings.append(txt)
        headings += re.findall(r"\bt\(\s*\"[\w.-]*(?:title|heading)[\w.-]*\"\s*,\s*\"([^\"]{3,120})\"", src)
        copy = [s.strip() for s in re.findall(r">\s*([A-Z][^<>{}]{24,240}?)\s*<", src)]
        labels = re.findall(r"(?:title|label|eyebrow)=\"([^\"]{4,80})\"", src)
        # Translated copy: t("key", "English fallback") carries the visible English text.
        labels += re.findall(r"\bt\(\s*\"[\w.-]+\"\s*,\s*\"([^\"]{4,120})\"", src)
        top_comment = re.match(r'\s*(?:"use client";)?\s*/\*(.*?)\*/', src, re.S)
        desc = re.sub(r"\s*\*\s?", " ", top_comment.group(1)).strip()[:500] if top_comment else ""
        pages.append({"url": url, "file": os.path.relpath(os.path.join(base, "page.tsx"), ROOT).replace(os.sep, "/"),
                      "headings": list(dict.fromkeys(h.strip() for h in headings if h.strip()))[:12],
                      "labels": list(dict.fromkeys(l.strip() for l in labels if l.strip()))[:24],
                      "copy": list(dict.fromkeys(copy))[:10], "description": desc})
    pages.sort(key=lambda p: p["url"])
    chunks = []
    for p in pages:
        text = "; ".join(x for x in (p["description"], "Headings: " + ", ".join(p["headings"]) if p["headings"] else "",
                                      "Labels: " + ", ".join(p["labels"]) if p["labels"] else "",
                                      " ".join(p["copy"])) if x)
        chunks.append({"source": p["file"], "kind": "page", "title": f"Web page {p['url']}", "text": text[:MAX_CHUNK]})
    return pages, chunks


def build():
    docs = markdown_docs()
    mods = module_docstrings()
    routes, route_chunks = api_routes()
    pages, page_chunks = web_pages()
    chunks = docs + mods + route_chunks + page_chunks
    for i, c in enumerate(chunks):
        c["id"] = i
    body = {"version": 1, "chunks": chunks, "routes": routes, "pages": pages,
            "sources": sorted({c["source"] for c in chunks})}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    body["digest"] = digest
    return body


def source_paths():
    """Every file the artifact is derived from — used to decide whether it is stale."""
    paths = [os.path.join(ROOT, n) for n in os.listdir(ROOT) if n.lower().endswith(".md")]
    for base, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        paths += [os.path.join(base, f) for f in files if f.endswith(".py") and not f.startswith("test_")]
    app_dir = os.path.join(ROOT, "web", "src", "app")
    for base, _dirs, files in os.walk(app_dir):
        paths += [os.path.join(base, f) for f in files if f == "page.tsx"]
    return paths


def main(argv):
    built = build()
    if "--check" in argv:
        try:
            with open(ARTIFACT, encoding="utf-8") as fh:
                current = json.load(fh).get("digest")
        except (OSError, ValueError):
            current = None
        if current != built["digest"]:
            print(f"project_knowledge.json is stale (committed {current}, sources {built['digest']}). "
                  "Run `python build_project_knowledge.py` and commit the result.")
            return 1
        print(f"project knowledge up to date ({len(built['chunks'])} chunks, digest {built['digest']})")
        return 0
    os.makedirs(os.path.dirname(ARTIFACT), exist_ok=True)
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump(built, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"wrote {ARTIFACT}: {len(built['chunks'])} chunks from {len(built['sources'])} sources, "
          f"{len(built['routes'])} routes, {len(built['pages'])} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
