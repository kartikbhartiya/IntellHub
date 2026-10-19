"""The copilot's knowledge of the platform must track the platform.

The failure these guard against is silent drift: a page, route or document is added
and the copilot keeps describing the old product. The freshness test fails the
suite until `python build_project_knowledge.py` is rerun and its output committed;
the rest check that what is indexed can actually be found.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_project_knowledge as B  # noqa: E402
import copilot_knowledge as K  # noqa: E402

FULL_CHECKOUT = os.path.exists(os.path.join(B.ROOT, "README.md")) and os.path.isdir(
    os.path.join(B.ROOT, "web", "src", "app"))


@pytest.mark.skipif(not FULL_CHECKOUT, reason="docs and web client are not deployed with appsail/")
def test_committed_knowledge_matches_the_repository():
    import json
    with open(B.ARTIFACT, encoding="utf-8") as fh:
        committed = json.load(fh)["digest"]
    assert committed == B.build()["digest"], (
        "artifacts/project_knowledge.json is stale — run `python build_project_knowledge.py` and commit it")


@pytest.mark.skipif(not FULL_CHECKOUT, reason="needs the full repository")
def test_every_web_page_and_api_route_is_indexed():
    built = B.build()
    urls = {p["url"] for p in built["pages"]}
    for url in ("/", "/cases", "/map", "/network", "/analytics", "/pendency", "/dispatch"):
        assert url in urls, url
    paths = {r["path"] for r in built["routes"]}
    for path in ("/serverless/api/agent", "/serverless/api/case/<case_id>", "/serverless/api/dispatch"):
        assert path in paths, path


def test_routes_carry_the_role_they_require():
    roles = {r["path"]: r["role"] for r in B.api_routes()[0]}
    assert roles["/serverless/api/audit-log"].startswith("audit_read") and "supervisor" in roles["/serverless/api/audit-log"]
    assert roles["/serverless/api/case/<case_id>"].startswith("view_cases") and "investigator" in roles["/serverless/api/case/<case_id>"]
    # Blueprint routes are indexed too, with their own module as the source.
    assert "/serverless/api/forecast/annual" in roles and "/serverless/api/fir/register" in roles


@pytest.mark.parametrize("question,expected_source", [
    ("how does the audit chain detect tampering", "README.md"),
    ("which api returns the dispatch plan", "appsail/main.py"),
    ("what does the pendency page show", "web/src/app/pendency/page.tsx"),
    ("how do I raise a contest against a score", "contest"),
])
def test_questions_find_the_document_that_answers_them(question, expected_source):
    hits = K.search(question, k=4)
    assert any(expected_source in h["source"] for h in hits), [h["source"] for h in hits]


def test_a_new_source_is_picked_up_without_editing_curated_topics(monkeypatch):
    """The point of the generated index: knowledge follows the code."""
    fake = {"chunks": [{"id": 0, "source": "NEW_FEATURE.md", "kind": "doc", "title": "Night patrol optimiser",
                        "text": "The night patrol optimiser schedules beats by predicted chain snatching risk."}],
            "routes": [], "pages": [], "sources": ["NEW_FEATURE.md"], "digest": "test"}
    monkeypatch.setattr(K, "_load_raw", lambda: fake)
    K.reload()
    try:
        hits = K.search("what does the night patrol optimiser do", k=2)
        assert hits and hits[0]["source"] == "NEW_FEATURE.md"
    finally:
        K.reload()


def test_scope_question_states_what_the_copilot_does_not_search():
    secs = K.lookup("can you search aadhaar or the internet for a person")
    assert secs[0]["topic"] == "scope"
    assert "does not search the internet" in secs[0]["text"]


def test_live_description_reads_the_running_system():
    import main
    K.configure(main.app)
    live = K.describe_live("routes")
    assert any(r["path"] == "/serverless/api/agent" for r in live["routes"])
    tables = K.describe_live("tables", {"firs": __import__("pandas").DataFrame({"case_id": ["1"]})})
    assert tables["tables"][0]["table"] == "firs" and tables["tables"][0]["rows"] == 1


def test_the_reasoning_prompt_carries_a_generated_platform_map():
    import copilot_engine as E
    prompt = E._system_prompt("", "briefing", "/", None)
    for url in ("/pendency", "/network", "/dispatch"):
        assert url in prompt
    assert "describe_platform" in prompt and "project_knowledge" in prompt
