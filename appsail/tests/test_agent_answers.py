"""Tests for the copilot's refusals and its grounded answers.

The two that carry the argument:

* `test_a_request_to_act_on_a_person_is_refused` — "arrest SUS-1001" used to match the
  suspect-id rule and quietly open the dossier, answering a request for action by
  performing a lookup. GOVERNANCE.md says no output authorises an action against an
  individual; the copilot is the surface most likely to be asked for one in exactly
  those words.
* `test_every_answer_carries_its_grounding` — a reply with numbers in it and no record
  of where they came from is indistinguishable from a fluent guess, which is the
  failure mode this whole project is built to avoid.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_answers  # noqa: E402


# ------------------------------------------------------------------- refusals
@pytest.mark.parametrize("question", [
    "is SUS-1001 guilty",
    "was SUS-1001 responsible for this",
    "are they the culprit",
])
def test_a_question_about_guilt_is_refused(question):
    reply = agent_answers.refusal_for(question)
    assert reply, f"no refusal for {question!r}"
    assert "court" in reply.lower()
    assert "evidence" in reply.lower()


@pytest.mark.parametrize("question", [
    "arrest SUS-1001",
    "detain SUS-1001",
    "should we raid this suspect",
])
def test_a_request_to_act_on_a_person_is_refused(question):
    reply = agent_answers.refusal_for(question)
    assert reply, f"no refusal for {question!r}"
    assert "authorise" in reply.lower() or "authorize" in reply.lower()


def test_a_risk_score_is_not_restated_as_a_prediction_about_a_person():
    reply = agent_answers.refusal_for("will SUS-1001 commit a crime again")
    assert reply
    assert "forecast" in reply.lower() or "cohort" in reply.lower()


@pytest.mark.parametrize("question", [
    "open FIR 100070034202600005",
    "how many cases are there",
    "show the heatmap",
    "which district has the most crime",
])
def test_ordinary_questions_are_not_refused(question):
    """A refusal that fires on normal work would make the copilot useless, which is
    its own kind of failure."""
    assert agent_answers.refusal_for(question) is None


# -------------------------------------------------------- grounded answers
@pytest.fixture(scope="module")
def cache():
    from main import app, data_cache
    app.config["TESTING"] = True
    app.test_client().get("/serverless/api/health")
    return data_cache


@pytest.mark.parametrize("question", [
    "how many cases are there",
    "which district has the most crime",
    "what is the conviction rate",
    "how many murders in Belagavi",
    "how accurate is the recidivism model",
])
def test_factual_questions_are_answered_from_the_corpus(cache, question):
    """Every one of these used to fall through to "I could not reach my reasoning
    core" while the number sat in data_cache."""
    result = agent_answers.answer(question, cache)
    assert result, f"no answer for {question!r}"
    assert len(result["reply"]) > 40


def test_every_answer_carries_its_grounding(cache):
    """A number with no record of where it came from cannot be checked, and an
    unverifiable number is the thing this platform refuses to produce anywhere else."""
    for question in ("how many cases are there",
                     "which district has the most crime",
                     "what is the conviction rate",
                     "what does the audit log show"):
        result = agent_answers.answer(question, cache)
        assert result, question
        grounding = result.get("grounding")
        assert grounding, f"{question!r} answered with no grounding"
        assert grounding.get("source"), f"{question!r} does not name its source"


def test_the_answer_matches_the_data_it_claims_to_summarise(cache):
    """The count in the reply has to be the count in the frame — a plausible number
    is not the same as the right one."""
    firs = cache.get("firs")
    result = agent_answers.answer("how many cases are there", cache)
    assert result["grounding"]["cases"] == len(firs)
    assert f"{len(firs):,}" in result["reply"]


def test_a_filtered_count_is_actually_filtered(cache):
    firs = cache.get("firs")
    result = agent_answers.answer("how many murders in Belagavi", cache)
    assert result
    g = result["grounding"]
    expected = len(firs[(firs["crime_type"] == g["crime_type"])
                        & (firs["district"] == g["district"])])
    assert g["matched"] == expected


def test_a_watchlist_question_does_not_print_names(cache):
    """The route already requires investigator, so this is not about access — a chat
    transcript is the wrong place for a list of people, and it is the artefact most
    likely to be screenshotted."""
    result = agent_answers.answer("top 5 rowdy sheeters", cache)
    if not result:
        pytest.skip("no watchlist in this corpus")
    assert result["grounding"]["names_withheld"] is True
    assert result["action"] == "show_registry"
    suspects = cache.get("suspects")
    if suspects is not None and "name" in suspects:
        for name in suspects["name"].dropna().astype(str).head(50):
            assert name not in result["reply"], f"leaked {name!r} into the transcript"


def test_an_unanswerable_question_falls_through(cache):
    """Returning None is how the caller knows to try something else. A handler that
    invents an answer rather than declining is the whole problem."""
    assert agent_answers.answer("what is the weather in Paris", cache) is None
    assert agent_answers.answer("write me a poem", cache) is None


def test_a_broken_handler_cannot_take_the_copilot_down(cache, monkeypatch):
    """One bad handler must not turn every question into a 500."""
    def explode(text, c):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_answers, "_HANDLERS", (explode, agent_answers._corpus_size))
    result = agent_answers.answer("how many cases are there", cache)
    assert result and result["grounding"]["cases"]
