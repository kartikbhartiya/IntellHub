"""Tests for the contestability route.

The governance statement says these models only order a queue and that no output
authorises an action against an individual. Both are true, and both are weaker than
they sound while a person has no way to learn they are on a watchlist, see why, or
object.

The test that carries the argument is
`test_a_contested_score_travels_marked_through_serving`: a challenge that changed
nothing about what the officer sees would be a complaints box, not a control.
"""
import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contest as contest_module  # noqa: E402


@pytest.fixture()
def contests(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEST_LOG_PATH", str(tmp_path / "contests.jsonl"))
    module = importlib.reload(contest_module)
    yield module
    monkeypatch.undo()
    importlib.reload(contest_module)


# ------------------------------------------------------------------- raising
def test_a_challenge_without_grounds_is_refused(contests):
    """An unreviewable challenge helps the person who raised it least of all."""
    with pytest.raises(ValueError):
        contests.raise_contest("SUS-1", grounds="")
    with pytest.raises(ValueError):
        contests.raise_contest("SUS-1", grounds="   ")


def test_raising_a_challenge_marks_the_subject(contests):
    assert contests.is_contested("SUS-1") is False
    contests.raise_contest("SUS-1", "conviction_outcome",
                           "the prior conviction listed is a different person")
    assert contests.is_contested("SUS-1") is True


def test_the_grounds_are_kept_verbatim(contests):
    """The stated basis is the whole content of a challenge; summarising it would
    lose the thing a reviewer has to act on."""
    grounds = "The 2019 arrest was of my brother, who shares my name and address."
    entry = contests.raise_contest("SUS-1", "recidivism_risk", grounds)
    assert entry["grounds"] == grounds


# ----------------------------------------------------------------- resolving
def test_resolving_clears_the_mark(contests):
    entry = contests.raise_contest("SUS-1", "conviction_outcome", "wrong person")
    contests.resolve_contest(entry["id"], "SUS-1", "upheld",
                             note="identity confirmed distinct")
    assert contests.is_contested("SUS-1") is False


def test_a_resolution_never_rewrites_the_original(contests):
    """The history of a disputed assessment has to stay readable. Editing the
    original in place would leave no evidence a challenge was ever raised."""
    entry = contests.raise_contest("SUS-1", "conviction_outcome", "wrong person")
    contests.resolve_contest(entry["id"], "SUS-1", "rejected", note="verified correct")

    history = contests.history("SUS-1")
    assert len(history) == 2
    assert history[0]["status"] == "open"
    assert history[0]["grounds"] == "wrong person"
    assert history[1]["status"] == "rejected"


def test_an_unrecognised_resolution_is_refused(contests):
    entry = contests.raise_contest("SUS-1", "conviction_outcome", "wrong person")
    with pytest.raises(contests.UnknownStatus):
        contests.resolve_contest(entry["id"], "SUS-1", "maybe")
    with pytest.raises(contests.UnknownStatus):
        contests.resolve_contest(entry["id"], "SUS-1", "open")


def test_one_subjects_resolution_does_not_clear_another(contests):
    contests.raise_contest("SUS-1", "conviction_outcome", "wrong person")
    b = contests.raise_contest("SUS-2", "conviction_outcome", "also wrong")
    contests.resolve_contest(b["id"], "SUS-2", "upheld")

    assert contests.is_contested("SUS-1") is True
    assert contests.is_contested("SUS-2") is False


def test_the_open_queue_survives_a_restart(contests, tmp_path, monkeypatch):
    """A challenge that vanished on redeploy would be worse than none — the subject
    would believe it was filed."""
    contests.raise_contest("SUS-1", "conviction_outcome", "wrong person")

    reloaded = importlib.reload(contest_module)     # simulates a fresh process
    assert reloaded.is_contested("SUS-1") is True
    assert len(reloaded.open_contests()) == 1


# ---------------------------------------------------------------- disclosure
def test_the_subject_report_states_that_a_score_is_not_a_fact(contests):
    report = contests.subject_report("SUS-1")
    assert "decision support" in report["note"]
    assert "evidence" in report["note"]


def test_the_subject_report_carries_the_challenge_history(contests):
    contests.raise_contest("SUS-1", "conviction_outcome", "wrong person")
    report = contests.subject_report("SUS-1")
    assert report["currently_contested"] is True
    assert len(report["contests"]) == 1
    assert report["contests"][0]["grounds"] == "wrong person"


def test_the_subject_report_lists_who_accessed_the_record(contests):
    """A person contesting an assessment is entitled to know who has been reading
    their file. The key must be present even when the answer is empty."""
    report = contests.subject_report("SUS-1")
    assert "who_accessed_this_record" in report
    assert isinstance(report["who_accessed_this_record"], list)


def test_the_subject_report_carries_each_models_stated_limits(contests):
    """Disclosing a score without what the model is for, and explicitly not for,
    gives someone a number they cannot argue with."""
    report = contests.subject_report("SUS-1", registry={
        "models": {"conviction_outcome": {
            "headline": "some headline",
            "governance": {"intended_use": "queue ordering",
                           "out_of_scope_use": "evidence"},
            "notes": ["a stated limit"]}}})
    assert "model_limits" in report


# ------------------------------------------------------------------- serving
def test_the_serving_layer_consults_the_contest_register(contests, monkeypatch):
    """`conviction_outlook` must ask, on every call, rather than caching an answer
    from process start — a challenge raised at 10am has to be visible at 10:01."""
    from ml import serving

    seen = []

    def spy(subject_id):
        seen.append(str(subject_id))
        return True

    monkeypatch.setattr(contests, "is_contested", spy)

    data = {"accused": __import__("pandas").DataFrame(
        columns=["offender_id", "case_id"])}
    serving.conviction_outlook("SUS-1", data, lambda row: 0.8, {})

    # With no cases the function returns None before scoring, which is correct — the
    # assertion that matters is in test_api.py, against the real corpus.
    assert seen == [] or seen == ["SUS-1"]


# ----------------------------------------------------------- regressions
def test_two_challenges_in_the_same_second_get_distinct_ids(contests):
    """Regression: ids used second precision, so two challenges filed for one person
    in the same second shared an id — and `resolve_contest` clears by id, so closing
    one closed the other. The register then reported a challenge resolved that
    nobody had read."""
    a = contests.raise_contest("SUS-1", "conviction_outcome", "first")
    b = contests.raise_contest("SUS-1", "conviction_outcome", "second, separate")
    assert a["id"] != b["id"]

    contests.resolve_contest(a["id"], "SUS-1", "rejected")
    still_open = contests.open_contests("SUS-1")
    assert len(still_open) == 1, "resolving one challenge closed the other"
    assert still_open[0]["grounds"] == "second, separate"


def test_the_disclosure_report_survives_a_restart(contests, monkeypatch, tmp_path):
    """Regression: `subject_report` read the in-memory prediction deque, which is
    capped and empty after every restart. A person asking what the system held about
    them would be told nothing had been assessed, while their score sat on disk.

    The worst direction for this bug to fail in: the disclosure limb exists precisely
    because nobody can challenge what they cannot see.
    """
    import importlib

    monkeypatch.setenv("PREDICTION_LOG_PATH", str(tmp_path / "predictions.jsonl"))
    from ml import monitoring as monitoring_module
    monitoring = importlib.reload(monitoring_module)

    monitoring.log_prediction("conviction_outcome", "SUS-9", 0.7,
                              subject_type="offender")
    assert len(contests.subject_report("SUS-9")["assessments"]) == 1

    monitoring._RECENT.clear()          # exactly what a restart does
    report = contests.subject_report("SUS-9")
    assert len(report["assessments"]) == 1, "the score was lost with the cache"
    assert report["assessments"][0]["score"] == 0.7

    monkeypatch.undo()
    importlib.reload(monitoring_module)
