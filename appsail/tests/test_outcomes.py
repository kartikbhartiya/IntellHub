"""Tests for the outcome feedback loop.

The loop's whole value is that it can say "the answers stopped being right" when
drift monitoring still reads `stable`. That only holds if the join is honest, so the
tests that matter most here are the ones about *which* prediction an outcome is
measured against:

* `test_an_outcome_is_never_measured_against_a_later_prediction` — the leakage guard.
  A subject is rescored as their record changes; pairing a known outcome with a score
  produced after the fact would manufacture accuracy out of nothing, which is exactly
  the failure the training pipeline's temporal splits exist to prevent.
* `test_an_immature_outcome_is_excluded` — "no reoffence" recorded a week after
  scoring is not evidence of anything.
"""
import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def loop(tmp_path, monkeypatch):
    """Isolated prediction and outcome logs, and the two modules bound to them."""
    monkeypatch.setenv("PREDICTION_LOG_PATH", str(tmp_path / "predictions.jsonl"))
    monkeypatch.setenv("OUTCOME_LOG_PATH", str(tmp_path / "outcomes.jsonl"))

    from ml import monitoring as monitoring_module
    from ml import outcomes as outcomes_module
    monitoring = importlib.reload(monitoring_module)
    outcomes = importlib.reload(outcomes_module)

    yield monitoring, outcomes

    monkeypatch.undo()
    importlib.reload(monitoring_module)
    importlib.reload(outcomes_module)


def _predict(monitoring, subject, score, ts):
    """Append a prediction with a chosen timestamp."""
    entry = monitoring.log_prediction("conviction_outcome", subject, score,
                                      subject_type="offender")
    rows = []
    with open(monitoring.PREDICTION_LOG, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    rows[-1]["ts"] = ts
    with open(monitoring.PREDICTION_LOG, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return entry


# ------------------------------------------------------------------- vocabulary
def test_an_uninterpretable_outcome_is_refused(loop):
    """A free-text outcome column is unusable within a month, and there is no Brier
    score to compute against 'acquitted (appeal pending)'."""
    _, outcomes = loop
    with pytest.raises(outcomes.UnknownOutcome):
        outcomes.record_outcome("conviction_outcome", "SUS-1", "maybe")


def test_a_model_with_no_outcome_vocabulary_is_refused(loop):
    _, outcomes = loop
    with pytest.raises(outcomes.UnknownOutcome):
        outcomes.record_outcome("hotspot_clusters", "BLR-1", "convicted")


def test_a_recorded_outcome_carries_its_numeric_label(loop):
    _, outcomes = loop
    entry = outcomes.record_outcome("conviction_outcome", "SUS-1", "acquitted")
    assert entry["label"] == 0
    entry = outcomes.record_outcome("conviction_outcome", "SUS-2", "convicted")
    assert entry["label"] == 1


# ------------------------------------------------------------------- the join
def test_an_outcome_is_never_measured_against_a_later_prediction(loop):
    """The leakage guard.

    A subject is scored, the case resolves, and then the subject is scored again with
    the resolution now visible in their record. Measuring the known outcome against
    that second score would manufacture accuracy from information the first score
    never had.
    """
    monitoring, outcomes = loop
    _predict(monitoring, "SUS-1", 0.20, "2026-01-01T00:00:00+00:00")   # before
    _predict(monitoring, "SUS-1", 0.95, "2026-09-01T00:00:00+00:00")   # after

    outcomes.record_outcome("conviction_outcome", "SUS-1", "convicted",
                            observed_at="2026-06-01T00:00:00+00:00")

    pairs = outcomes.join("conviction_outcome")["pairs"]
    assert len(pairs) == 1
    assert pairs[0]["score"] == 0.2, "the post-hoc score was used"


def test_the_most_recent_prediction_before_the_outcome_is_used(loop):
    """Among scores that all predate the observation, the latest is the live one."""
    monitoring, outcomes = loop
    _predict(monitoring, "SUS-1", 0.10, "2026-01-01T00:00:00+00:00")
    _predict(monitoring, "SUS-1", 0.40, "2026-03-01T00:00:00+00:00")

    outcomes.record_outcome("conviction_outcome", "SUS-1", "convicted",
                            observed_at="2026-06-01T00:00:00+00:00")

    pairs = outcomes.join("conviction_outcome")["pairs"]
    assert pairs[0]["score"] == 0.4


def test_an_outcome_for_an_unscored_subject_is_counted_not_dropped(loop):
    """Silently discarding these would hide the case where outcomes are recorded for
    people the system never scored — which means the log is being written by hand
    against the wrong identifiers."""
    _, outcomes = loop
    outcomes.record_outcome("conviction_outcome", "SUS-NEVER-SCORED", "convicted")

    joined = outcomes.join("conviction_outcome")
    assert joined["pairs"] == []
    assert joined["outcomes_without_a_prediction"] == 1


def test_the_observation_wait_is_recorded(loop):
    monitoring, outcomes = loop
    _predict(monitoring, "SUS-1", 0.5, "2026-01-01T00:00:00+00:00")
    outcomes.record_outcome("conviction_outcome", "SUS-1", "convicted",
                            observed_at="2026-07-01T00:00:00+00:00")

    pairs = outcomes.join("conviction_outcome")["pairs"]
    assert pairs[0]["observation_days"] == pytest.approx(181.0, abs=1.0)


# ------------------------------------------------------------------- maturity
def test_an_immature_outcome_is_excluded(loop):
    """"No reoffence" a week after scoring measures nothing. It must not be allowed
    to inflate a realised accuracy figure."""
    monitoring, outcomes = loop
    _predict(monitoring, "SUS-1", 0.9, "2026-08-01T00:00:00+00:00")
    outcomes.record_outcome("conviction_outcome", "SUS-1", "convicted",
                            observed_at="2026-08-08T00:00:00+00:00")

    report = outcomes.realised("conviction_outcome")
    assert report["matched_to_a_prediction"] == 1
    assert report["matured"] == 0
    assert report["not_yet_matured"] == 1
    assert report["verdict"] == "no outcomes recorded yet"


# ------------------------------------------------------------------- realised
def _seed(monitoring, outcomes, n=40, accurate=True):
    """n subjects, scored then resolved a year later."""
    for i in range(n):
        convicted = i % 2 == 0
        score = (0.9 if convicted else 0.1) if accurate else 0.5
        _predict(monitoring, f"SUS-{i}", score, "2025-01-01T00:00:00+00:00")
        outcomes.record_outcome("conviction_outcome", f"SUS-{i}",
                                "convicted" if convicted else "acquitted",
                                observed_at="2026-06-01T00:00:00+00:00")


def test_realised_metrics_are_computed_once_there_are_enough_outcomes(loop):
    monitoring, outcomes = loop
    _seed(monitoring, outcomes, n=40)

    report = outcomes.realised("conviction_outcome")
    assert report["sufficient"] is True
    assert report["matured"] == 40
    assert report["accuracy_at_threshold"] == 1.0
    assert report["brier"] == pytest.approx(0.01, abs=1e-6)
    assert report["observed_positive_rate"] == 0.5


def test_a_thin_sample_is_reported_but_refuses_a_verdict(loop):
    """Below ~30 pairs the gap between realised and published Brier is sampling
    noise. Reporting it as a finding invites a retrain on a swing that never
    happened."""
    monitoring, outcomes = loop
    _seed(monitoring, outcomes, n=6)

    report = outcomes.realised("conviction_outcome")
    assert report["matured"] == 6
    assert report["sufficient"] is False
    assert report["minimum_for_a_verdict"] == 30
    # Skill and published-agreement are separate questions now, so each is checked
    # in its own field: six pairs support neither answer.
    if "comparison" in report:
        assert report["comparison"]["verdict"].startswith("not enough matured")
        assert report["comparison"]["vs_published"] == (
            "too few matured outcomes to compare against published")


def test_a_model_that_has_stopped_working_is_visible(loop):
    """The point of the whole module: a model whose answers stopped coming true.

    Every score is 0.5 while outcomes split evenly, so the Brier score is 0.25 —
    no better than a coin. Drift monitoring would report `stable` throughout,
    because the inputs never moved.
    """
    monitoring, outcomes = loop
    _seed(monitoring, outcomes, n=40, accurate=False)

    report = outcomes.realised("conviction_outcome")
    assert report["brier"] == pytest.approx(0.25, abs=1e-6)
    # Every score is 0.5 against a 0.5 base rate, so the model IS the base rate and
    # its skill is exactly zero. "Indistinguishable from predicting the base rate" is
    # the accurate reading; the earlier assertion of "worse" was the loop claiming
    # more than the arithmetic supports.
    if report.get("comparison"):
        assert report["comparison"]["verdict"].startswith("indistinguishable")
    assert report["skill_score"] == pytest.approx(0.0, abs=1e-6)


def test_calibration_is_reported_per_band(loop):
    """A model can be well calibrated on average and badly wrong at the top, which
    is the only end anyone acts on."""
    monitoring, outcomes = loop
    _seed(monitoring, outcomes, n=40)

    bands = outcomes.realised("conviction_outcome")["calibration"]
    assert len(bands) == 5
    populated = [b for b in bands if b["n"]]
    assert populated, "no band received any pair"
    for band in populated:
        assert 0.0 <= band["observed_rate"] <= 1.0


def test_the_comparison_states_that_outcomes_are_not_a_random_sample(loop):
    """Cases that reach a result differ systematically from those that do not.
    Presenting realised metrics without that caveat would be the same class of
    error as the fabricated panels removed from the frontend."""
    monitoring, outcomes = loop
    _seed(monitoring, outcomes, n=40)

    report = outcomes.realised("conviction_outcome")
    if report.get("comparison"):
        assert "random sample" in report["comparison"]["caveat"]


# ------------------------------------------------------------------- coverage
def test_an_empty_loop_says_so_rather_than_returning_zeros(loop):
    """A model with no outcomes must not read as a model with perfect ones."""
    _, outcomes = loop
    report = outcomes.realised("conviction_outcome")
    assert report["verdict"] == "no outcomes recorded yet"
    assert "brier" not in report, "a metric was reported with nothing to compute it on"
    assert "drift" in report["note"].lower() or "inputs moved" in report["note"]


def test_coverage_reports_the_outcome_rate_per_model(loop):
    monitoring, outcomes = loop
    _predict(monitoring, "SUS-1", 0.5, "2025-01-01T00:00:00+00:00")
    _predict(monitoring, "SUS-2", 0.5, "2025-01-01T00:00:00+00:00")
    outcomes.record_outcome("conviction_outcome", "SUS-1", "convicted",
                            observed_at="2026-06-01T00:00:00+00:00")

    row = outcomes.coverage()["models"]["conviction_outcome"]
    assert row["predictions_logged"] == 2
    assert row["outcomes_recorded"] == 1
    assert row["outcome_rate"] == 0.5
    assert row["accepts_outcomes"] is True


def test_every_model_that_accepts_outcomes_declares_a_window(loop):
    """A window of zero would let an outcome recorded the same day count as
    evidence."""
    _, outcomes = loop
    for name in outcomes.LABELS:
        assert outcomes.MIN_OBSERVATION_DAYS.get(name, 0) > 0, name


# ----------------------------------------------------------- regressions
def test_a_prediction_that_rolled_is_still_matched(loop, tmp_path):
    """Regression: only the live log was read, so any score that rolled out of it was
    silently counted as an outcome with no prediction.

    Not an edge case. Both sinks roll at 16MB and the observation windows are 180 to
    365 days, so in the ordinary case a prediction rolls long before its outcome is
    known. Realised performance then quietly narrowed to whatever was recent — a
    measurement biased toward the newest cases, reported as if it covered all of them.
    """
    import os

    monitoring, outcomes = loop
    _predict(monitoring, "SUS-ROLLED", 0.9, "2025-01-01T00:00:00+00:00")
    os.replace(monitoring.PREDICTION_LOG, monitoring.PREDICTION_LOG + ".1")

    outcomes.record_outcome("conviction_outcome", "SUS-ROLLED", "convicted",
                            observed_at="2026-06-01T00:00:00+00:00")

    joined = outcomes.join("conviction_outcome")
    assert joined["outcomes_without_a_prediction"] == 0
    assert len(joined["pairs"]) == 1
    assert joined["pairs"][0]["score"] == 0.9


def test_coverage_counts_across_a_rotation(loop):
    """The same blindness inflated the outcome rate: predictions in the rolled file
    went uncounted, so a thinly covered model looked well covered."""
    import os

    monitoring, outcomes = loop
    _predict(monitoring, "SUS-1", 0.5, "2025-01-01T00:00:00+00:00")
    os.replace(monitoring.PREDICTION_LOG, monitoring.PREDICTION_LOG + ".1")
    _predict(monitoring, "SUS-2", 0.5, "2025-06-01T00:00:00+00:00")

    row = outcomes.coverage()["models"]["conviction_outcome"]
    assert row["predictions_logged"] == 2


# ------------------------------------------------------- provenance / replay
def test_replayed_outcomes_are_never_reported_as_operational(loop):
    """A replayed backtest and a result an officer recorded produce identical
    numbers and mean entirely different things. The provenance has to travel with
    the metric, not sit in a caption somewhere else."""
    monitoring, outcomes = loop
    for i in range(40):
        convicted = i % 2 == 0
        _predict(monitoring, f"SUS-{i}", 0.9 if convicted else 0.1,
                 "2025-01-01T00:00:00+00:00")
        outcomes.record_outcome("conviction_outcome", f"SUS-{i}",
                                "convicted" if convicted else "acquitted",
                                observed_at="2026-06-01T00:00:00+00:00",
                                source="replay")

    report = outcomes.realised("conviction_outcome")
    assert report["sources"] == {"replay": 40}
    assert report["operational_outcomes"] == 0
    assert "provenance_warning" in report
    assert "not from operational use" in report["provenance_warning"]         or "NOT evidence of field performance" in report["provenance_warning"]


def test_one_real_outcome_removes_the_replay_only_warning(loop):
    """The warning is about the absence of operational evidence, so a single real
    result must clear it — otherwise it would keep crying wolf once the loop is
    genuinely in use."""
    monitoring, outcomes = loop
    for i in range(40):
        _predict(monitoring, f"SUS-{i}", 0.9, "2025-01-01T00:00:00+00:00")
        outcomes.record_outcome("conviction_outcome", f"SUS-{i}", "convicted",
                                observed_at="2026-06-01T00:00:00+00:00",
                                source="replay")
    _predict(monitoring, "SUS-REAL", 0.9, "2025-01-01T00:00:00+00:00")
    outcomes.record_outcome("conviction_outcome", "SUS-REAL", "convicted",
                            observed_at="2026-06-01T00:00:00+00:00", source="api")

    report = outcomes.realised("conviction_outcome")
    assert report["operational_outcomes"] == 1
    assert "provenance_warning" not in report


def test_the_replay_plan_matches_the_outcome_vocabulary():
    """`replay_outcomes.py` writes labels straight into the loop, so a label the
    vocabulary does not recognise would be refused at write time and the replay would
    half-populate before failing."""
    import replay_outcomes
    from ml import outcomes as o

    for name, plan in replay_outcomes.PLANS.items():
        assert name in o.LABELS, name
        for label in plan["outcome"].values():
            assert label in o.LABELS[name], f"{name}: {label!r} not in the vocabulary"
        assert name in o.MIN_OBSERVATION_DAYS, name


# ------------------------------------------------ verdicts and sample size
def _seed_noisy(monitoring, outcomes, n, edge=0.02):
    """n pairs with a real but tiny edge over the base rate — the case where the
    effect and the sampling noise are the same size."""
    import random
    rng = random.Random(7)
    for i in range(n):
        convicted = rng.random() < 0.5
        score = 0.5 + (edge if convicted else -edge)
        _predict(monitoring, f"SUS-{i}", round(score, 4), "2025-01-01T00:00:00+00:00")
        outcomes.record_outcome("conviction_outcome", f"SUS-{i}",
                                "convicted" if convicted else "acquitted",
                                observed_at="2026-06-01T00:00:00+00:00")


def test_a_small_sample_refuses_to_render_a_verdict(loop):
    """Regression: the loop declared a model "contributing no skill" whenever the
    point-estimate skill was negative and there were more than thirty pairs.

    Measured on the live deployment, 905 pairs gave +0.077 and a random 146 of the
    same pairs gave -0.024 — the effect and its noise are the same size, so the sign
    flips on resampling. A count threshold cannot catch that; the interval can.
    """
    monitoring, outcomes = loop
    _seed_noisy(monitoring, outcomes, 60)

    report = outcomes.realised("conviction_outcome")
    interval = report["skill_interval"]
    lo, hi = interval["ci95"]
    assert lo < 0 < hi, "this sample should not support a conclusion either way"
    assert report["comparison"]["verdict"].startswith("indistinguishable")


def test_a_large_sample_detects_real_skill(loop):
    """The check must not be so cautious it can never say anything: with enough
    pairs and a genuine edge, the interval has to clear zero."""
    monitoring, outcomes = loop
    for i in range(400):
        convicted = i % 2 == 0
        _predict(monitoring, f"SUS-{i}", 0.85 if convicted else 0.15,
                 "2025-01-01T00:00:00+00:00")
        outcomes.record_outcome("conviction_outcome", f"SUS-{i}",
                                "convicted" if convicted else "acquitted",
                                observed_at="2026-06-01T00:00:00+00:00")

    report = outcomes.realised("conviction_outcome")
    lo, hi = report["skill_interval"]["ci95"]
    assert lo > 0, "a strongly skilful model should clear zero"
    assert report["comparison"]["verdict"].startswith("beats the base-rate")


def test_a_genuinely_useless_model_is_still_called_out(loop):
    """The interval must not become a way of never delivering bad news. A model
    scored well below the base rate, with enough pairs, has to be named."""
    monitoring, outcomes = loop
    for i in range(400):
        convicted = i % 2 == 0
        # Confidently wrong: high score when the answer is no, and vice versa.
        _predict(monitoring, f"SUS-{i}", 0.15 if convicted else 0.85,
                 "2025-01-01T00:00:00+00:00")
        outcomes.record_outcome("conviction_outcome", f"SUS-{i}",
                                "convicted" if convicted else "acquitted",
                                observed_at="2026-06-01T00:00:00+00:00")

    report = outcomes.realised("conviction_outcome")
    lo, hi = report["skill_interval"]["ci95"]
    assert hi < 0, "a confidently wrong model should fall entirely below zero"
    assert "no skill" in report["comparison"]["verdict"]
