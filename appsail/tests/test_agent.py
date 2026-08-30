"""Golden cases for the intent router.

The conversational agent is roughly a third of `main.py` — trilingual lexicons, a
116-line heuristic router, a 78-line deterministic matcher, superlative resolution,
LLM provider fallback — and had no test of any kind. It is also the component most
likely to be edited under time pressure, and the one whose regressions are least
visible: a broken intent does not raise, it just quietly routes somewhere else.

These are pinned behaviours, not aspirations. Each case is one an operator would
plausibly type, with the routing the platform currently commits to. If a change
moves one of these, that is a decision to make deliberately rather than discover
in a demo.

The LLM path is not exercised here — it requires a provider and is non-deterministic.
What is tested is the deterministic layer that runs first and the fallback that runs
when the model is unreachable, which together are what the platform actually relies
on when the network is down.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grounding  # noqa: E402
import main  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def loaded():
    main.load_and_build_intelligence()


# ---------------------------------------------------------------- entity extraction
@pytest.mark.parametrize("text,expected", [
    ("crimes in mysuru last week", "Mysuru"),
    ("show Bengaluru Urban heatmap", "Bengaluru Urban"),
    ("dakshina kannada cases", "Dakshina Kannada"),
    ("no district mentioned here", None),
])
def test_district_is_detected_case_insensitively(text, expected):
    assert main._detect_district(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("chain snatching cases", "Chain Snatching"),
    ("show vehicle theft", "Vehicle Theft"),
    ("nothing relevant", None),
])
def test_crime_type_is_detected(text, expected):
    assert main._detect_crime_type(text) == expected


def test_district_detection_does_not_match_a_commissionerate():
    """Mangaluru and Hubballi-Dharwad are commissionerates, not districts. Matching
    them would route to a jurisdiction the corpus cannot answer for."""
    import karnataka as KA
    for name in ("Mangaluru", "Hubballi-Dharwad"):
        detected = main._detect_district(f"show {name} crime")
        assert detected is None or detected in KA.DISTRICT_NAMES


# ---------------------------------------------------------------- identifiers
def test_case_id_normalisation_resolves_to_a_real_case():
    resolved = main.normalize_case_id("20345")
    assert resolved
    firs = main.data_cache["firs"]
    assert (firs["case_id"] == str(resolved)).any(), "normalised to a non-existent case"


def test_unknown_case_number_does_not_invent_one():
    assert not main.normalize_case_id("99999999999")


def test_suspect_id_normalisation():
    assert main.normalize_suspect_id("1001") in ("SUS-1001", None)


# ---------------------------------------------------------------- routing
@pytest.mark.parametrize("text,action", [
    ("show heatmap for Bengaluru Urban", "show_heatmap"),
    ("what are the top hotspots", "show_heatmap"),
    ("open FIR-20345", "open_case"),
    ("show me suspect SUS-1001", "open_suspect"),
    ("recidivism risk scores", "show_analytics"),
    ("patrol dispatch plan", "show_dispatch"),
])
def test_deterministic_router_pins_common_intents(text, action):
    result = main._deterministic_intent(text)
    assert result is not None, f"no intent matched {text!r}"
    assert result["action"] == action


def test_heatmap_intent_carries_the_district_through():
    result = main._deterministic_intent("show heatmap for Bengaluru Urban")
    assert result["params"].get("district") == "Bengaluru Urban"


def test_open_case_resolves_the_identifier_not_the_raw_text():
    result = main._deterministic_intent("open FIR-20345")
    assert result["action"] == "open_case"
    case_id = result["params"]["case_id"]
    assert case_id != "20345", "the raw number was passed through unresolved"
    assert (main.data_cache["firs"]["case_id"] == str(case_id)).any()


# ---------------------------------------------------------------- guards
@pytest.mark.parametrize("value", ["null", "none", "n/a", "undefined", "", None])
def test_placeholder_parameters_are_rejected(value):
    """An LLM that cannot fill a slot tends to emit the string 'null'. Routing on it
    navigates to a page for a case that does not exist."""
    assert not main._valid_param(value)


@pytest.mark.parametrize("value", ["Mysuru", "SUS-1001", "Chain Snatching"])
def test_real_parameters_are_accepted(value):
    assert main._valid_param(value)


def test_agent_survives_an_unreachable_model(monkeypatch):
    """With no provider reachable the agent must still answer from the deterministic
    layer rather than failing the request."""
    monkeypatch.setattr(main, "llm_available", lambda: False)
    result = main.run_agent("show heatmap for Mysuru")
    assert result["action"] == "show_heatmap"
    assert result["params"].get("district") == "Mysuru"
    assert result.get("reply")


def test_agent_answers_something_for_an_unrecognised_query(monkeypatch):
    monkeypatch.setattr(main, "llm_available", lambda: False)
    result = main.run_agent("qwertyuiop asdfgh")
    assert result.get("action")
    assert result.get("reply"), "the agent returned no reply at all"


def test_agent_never_returns_an_unknown_action(monkeypatch):
    """The client switches on `action`; an unrecognised value silently does nothing.
    Only the accidental completeness of that switch contained it before."""
    monkeypatch.setattr(main, "llm_available", lambda: False)
    known = {
        "open_case", "open_suspect", "show_heatmap", "show_network", "show_dispatch",
        "show_anomalies", "show_analytics", "recent_cases", "show_registry",
        "show_suspects", "open_copilot", "guide", "answer", "investigate",
        "show_lifecycle", "show_pendency", "show_models", "show_map",
    }
    for query in ("show heatmap", "open FIR-20345", "who is the top offender",
                  "patrol plan", "random nonsense text"):
        action = main.run_agent(query).get("action")
        assert action in known, f"{query!r} produced unknown action {action!r}"


# ---------------------------------------------------------------- grounding
def test_a_real_identifier_verifies():
    case_id = main.data_cache["firs"].iloc[0]["case_id"]
    report = grounding.check(f"Case {case_id} remains under investigation.", main.data_cache)
    assert report["grounded"] and report["verified"] == 1


def test_an_invented_case_number_is_caught():
    """The failure that matters: a fluent sentence citing a case that does not exist.
    An officer cannot tell it from a true one."""
    report = grounding.check("Case 100060019209900001 is linked to SUS-999999.",
                             main.data_cache)
    assert report["grounded"] is False
    assert {u["kind"] for u in report["unknown"]} == {"case", "offender"}


def test_an_unverified_answer_carries_its_caution_in_the_text():
    """Not in a sibling JSON field: the reply is spoken and copied around, and a
    flag in the envelope survives neither."""
    text = "Case 100060019209900001 is the strongest lead."
    annotated = grounding.annotate(text, grounding.check(text, main.data_cache))
    assert annotated.startswith("[unverified")
    assert text in annotated


def test_a_grounded_answer_is_left_alone():
    case_id = main.data_cache["firs"].iloc[0]["case_id"]
    text = f"Case {case_id} is open."
    assert grounding.annotate(text, grounding.check(text, main.data_cache)) == text


def test_missing_registry_does_not_accuse_the_model():
    """Inability to verify is not the same as unverified."""
    report = grounding.check("Case 12345678901234567 exists.",
                             {"firs": None, "suspects": None, "officers": None})
    assert report["grounded"] is True and report["checked"] == 0


@pytest.mark.parametrize("action,expected", [
    ("open_case", "open_case"),
    ("show_heatmap", "show_heatmap"),
    ("rm_rf_database", "answer"),
    ("", "answer"),
])
def test_unknown_actions_degrade_to_answer(action, expected):
    assert grounding.safe_action(action) == expected


def test_agent_response_reports_its_grounding(monkeypatch):
    monkeypatch.setattr(main, "llm_available", lambda: False)
    result = main.run_agent("show heatmap for Mysuru")
    assert "grounding" in result
    assert result["grounding"]["grounded"] is True
