"""Tests for the ML layer.

These are mostly *guard* tests. The failures they are written to catch are the
ones that do not announce themselves: a split that lets a model see the future,
a banned feature creeping back into a feature list, a score that changes between
two runs on identical data. All of those produce a working system with wrong
numbers, which is the worst failure mode a system like this has.

Run:  python -m pytest tests -q
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
from ml import (baselines, config, dataset, evaluate, monitoring, registry,  # noqa: E402
                splits, survival)
from ml.tasks import allocation, conviction, hotspots, narrative, pendency, recidivism  # noqa: E402


@pytest.fixture(scope="module")
def data():
    return dataset.load_canonical()


# ------------------------------------------------------------------ adapter
def test_canonical_frames_present(data):
    for key in ("cases", "reports", "offenders", "accused", "courts",
                "officers", "arrests", "chargesheets"):
        assert key in data, f"canonical frame missing: {key}"
        assert isinstance(data[key], pd.DataFrame)


def test_cases_have_required_columns(data):
    required = {"case_id", "case_key", "district", "crime_type", "ts",
                "severity_score", "status", "latitude", "longitude"}
    assert required <= set(data["cases"].columns)


def test_case_ids_unique_and_non_null(data):
    cases = data["cases"]
    assert cases["case_key"].notna().all()
    assert cases["case_key"].is_unique
    assert cases["case_id"].is_unique


def test_incidents_land_in_their_own_district(data):
    """A Belagavi FIR must not plot in Bengaluru.

    The original corpus drew every coordinate from one uniform box, so all
    districts shared a mean position and the map was decorative.

    This asserts the property that actually matters rather than a distance
    threshold: each district's incident centroid must be closer to *its own*
    headquarters than to any of the other 30. A fixed "centroids must be N km
    apart" rule fails on real geography — Koppal and Hosapete (Vijayanagara) are
    genuinely ~20 km apart, and Bengaluru Rural sits next to Chikkaballapura.
    Correct geography is not a test failure.
    """
    import karnataka as KA

    cases = data["cases"].dropna(subset=["latitude", "longitude"])
    centroids = cases.groupby("district")[["latitude", "longitude"]].mean()
    assert len(centroids) >= 2

    misplaced = []
    for name, row in centroids.iterrows():
        dists = {
            other: np.hypot(row["latitude"] - KA.centre(other)[0],
                            row["longitude"] - KA.centre(other)[1])
            for other in KA.DISTRICT_NAMES
        }
        nearest = min(dists, key=dists.get)
        if nearest != name:
            misplaced.append(f"{name} centroid sits nearest {nearest}")
    assert not misplaced, "; ".join(misplaced)


def test_geography_is_the_real_karnataka(data):
    """Guards the administrative facts a SCRB officer would check first."""
    import karnataka as KA

    assert len(KA.DISTRICTS) == 31, "Karnataka has 31 districts"
    assert len(KA.COMMISSIONERATES) == 8, "Karnataka Police has 8 commissionerates"

    # Cities are not districts. Both of these were in the corpus previously.
    assert "Mangaluru" not in KA.DISTRICTS, (
        "Mangaluru is the HQ of Dakshina Kannada and a commissionerate, not a district")
    assert "Hubballi-Dharwad" not in KA.DISTRICTS, (
        "Hubballi-Dharwad is a commissionerate; the district is Dharwad")
    for required in ("Dakshina Kannada", "Dharwad", "Vijayanagara", "Yadgir"):
        assert required in KA.DISTRICTS, f"missing district: {required}"

    # 2011 Census total, 6.11 crore.
    assert 600 <= sum(v[2] for v in KA.DISTRICTS.values()) <= 620

    # Every district in the corpus is a real one.
    unknown = set(data["cases"]["district"].unique()) - set(KA.DISTRICTS)
    assert not unknown, f"corpus contains non-districts: {sorted(unknown)}"


def test_case_volume_tracks_population(data):
    """Uniform district sampling gave Kodagu the same caseload as Bengaluru Urban,
    which makes every per-capita rate and district ranking meaningless."""
    import karnataka as KA

    counts = data["cases"]["district"].value_counts()
    pops = {d: KA.population(d) for d in counts.index}
    corr = np.corrcoef([counts[d] for d in counts.index],
                       [pops[d] for d in counts.index])[0, 1]
    assert corr > 0.9, f"case volume barely tracks population (r={corr:.2f})"
    assert counts["Bengaluru Urban"] > 5 * counts["Kodagu"], (
        "the largest district must carry substantially more cases than the smallest")


def test_registry_counts_agree_with_event_tables(data):
    """past_fir_count must match the number of cases actually naming the offender.

    In the original data these were independent random draws that contradicted
    the case records, which is precisely why they were unusable as features.
    """
    offenders, accused = data["offenders"], data["accused"]
    if offenders.empty or "past_fir_count" not in offenders.columns:
        pytest.skip("no offender registry")
    actual = accused.groupby("offender_id").size()
    merged = offenders.set_index("offender_id")["past_fir_count"]
    common = merged.index.intersection(actual.index)
    assert len(common) > 50
    assert (merged.loc[common] == actual.loc[common]).mean() > 0.99


# ------------------------------------------------------------------- splits
def test_temporal_split_never_leaks_the_future():
    df = pd.DataFrame({
        "ts": pd.date_range("2020-01-01", periods=500, freq="D"),
        "x": np.arange(500),
    })
    train, test, cutoff = splits.temporal_split(df, "ts")
    assert len(train) and len(test)
    assert train["ts"].max() <= test["ts"].min()
    assert cutoff is not None


def test_grouped_split_keeps_a_person_on_one_side_only():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "ts": pd.date_range("2020-01-01", periods=600, freq="D"),
        "person": rng.integers(0, 40, size=600),
    })
    train, test, _ = splits.grouped_temporal_split(df, "person", "ts")
    overlap = set(train["person"]) & set(test["person"])
    assert not overlap, f"{len(overlap)} people appear in both halves"


def test_walk_forward_folds_only_predict_forward():
    folds = splits.walk_forward_folds(200, n_folds=4, horizon=8, min_train=40)
    assert folds
    for origin, sl in folds:
        assert sl.start >= origin, "a fold scores on data it trained on"
        assert sl.stop <= 200


# ---------------------------------------------------------------- baselines
def test_majority_baseline_picks_the_majority():
    ytr = np.array(["a"] * 80 + ["b"] * 20)
    yte = np.array(["a"] * 7 + ["b"] * 3)
    out = baselines.majority_class_baseline(ytr, yte)
    assert out["predicts"] == "a"
    assert out["accuracy"] == pytest.approx(0.7)


def test_seasonal_naive_repeats_the_season():
    history = np.arange(104, dtype=float)
    out = baselines.seasonal_naive_forecast(history, horizon=4, period=52)
    assert np.allclose(out, history[-52:-48])


def test_naive_baseline_is_actually_hard_to_beat():
    """A flat series must not let the model claim skill it does not have."""
    history = np.full(60, 10.0)
    assert np.allclose(baselines.naive_forecast(history, 5), 10.0)


# ----------------------------------------------------------------- metrics
def test_perfect_prediction_scores_one():
    y = np.array([0, 1, 0, 1, 1, 0])
    m = evaluate.classification_metrics(y, y, labels=[0, 1])
    assert m["accuracy"] == 1.0
    assert m["f1_macro"] == 1.0


def test_confusion_matrix_rows_sum_to_support():
    y = np.array([0, 0, 1, 1, 1, 2])
    p = np.array([0, 1, 1, 1, 2, 2])
    m = evaluate.classification_metrics(y, p, labels=[0, 1, 2])
    for row, cls in zip(m["confusion_matrix"], m["per_class"]):
        assert sum(row) == cls["support"]


def test_calibration_detects_an_overconfident_model():
    rng = np.random.default_rng(1)
    y = rng.binomial(1, 0.3, size=2000)
    overconfident = np.where(y == 1, 0.99, 0.01)  # suspiciously certain
    honest = np.full(2000, 0.3)
    ece_bad = evaluate.reliability_bins(y, np.full(2000, 0.9))["expected_calibration_error"]
    ece_ok = evaluate.reliability_bins(y, honest)["expected_calibration_error"]
    assert ece_bad > ece_ok
    assert overconfident.max() <= 1.0


def test_lift_table_ranks_positives_first():
    y = np.array([1] * 20 + [0] * 80)
    p = np.concatenate([np.linspace(1.0, 0.9, 20), np.linspace(0.4, 0.0, 80)])
    rows = evaluate.lift_table(y, p, n_groups=5)
    assert rows[0]["lift"] > 1.0
    assert rows[-1]["cumulative_capture"] == pytest.approx(1.0)


def test_mase_below_one_means_better_than_naive():
    history = np.array([10.0, 12.0, 11.0, 13.0, 12.0, 14.0])
    truth = np.array([13.0, 13.0])
    good = np.array([13.0, 13.0])
    bad = np.array([0.0, 0.0])
    assert evaluate.mase(truth, good, history) < 1
    assert evaluate.mase(truth, bad, history) > 1


def test_interval_coverage_catches_a_broken_band():
    y = np.arange(10, dtype=float)
    tight = evaluate.interval_coverage(y, y + 0.4, y + 0.5)   # never contains y
    wide = evaluate.interval_coverage(y, y - 5, y + 5)
    assert tight == 0.0
    assert wide == 1.0


def test_json_safe_removes_numpy_and_nan():
    import json
    payload = {"a": np.int64(3), "b": np.float64(1.5), "c": np.float64("nan"),
               "d": [np.bool_(True)], "e": pd.Timestamp("2024-01-01")}
    json.dumps(evaluate.json_safe(payload))  # must not raise


# ---------------------------------------------------------- leakage guards
def test_banned_officer_features_are_absent_from_the_closure_model():
    """closure_rate and friends are aggregates of the label. They must stay out."""
    used = set(allocation.NUMERIC) | set(allocation.CATEGORICAL)
    assert not (used & config.BANNED_ALLOCATION_FEATURES)


def test_snapshot_flags_are_absent_from_the_recidivism_model():
    assert not (set(recidivism.BASE_FEATURES) & set(recidivism.SNAPSHOT_FEATURES))
    for undated in ("past_fir_count", "past_arrests", "risk_level"):
        assert undated not in recidivism.BASE_FEATURES


def test_conviction_model_excludes_undated_registry_flags():
    for undated in ("rowdy_sheeter", "history_sheet", "active_warrant"):
        assert undated not in conviction.FEATURES


def test_narrative_cleaning_strips_the_metadata_tag():
    raw = ("The complainant appeared before Camp PS. "
           "Booked under IPC Sec 384. "
           "[METADATA: SEV=26;WPN=None;VEH=Royal Enfield;TGT=Transit Hub;FAT=0;INJ=0;WIT=0]")
    cleaned = narrative.clean_text(raw)
    assert "METADATA" not in cleaned
    assert "SEV=" not in cleaned
    assert "IPC Sec 384" not in cleaned
    assert "complainant" in cleaned


def test_narrative_corpus_does_not_contain_the_label_verbatim(data):
    """A narrative naming its own crime head would make the classifier circular."""
    merged = data["reports"].merge(
        data["cases"][["case_id", "crime_type"]], on="case_id", how="inner")
    if merged.empty:
        pytest.skip("no narrative corpus")
    sample = merged.sample(min(800, len(merged)), random_state=0)
    hits = sample.apply(
        lambda r: str(r["crime_type"]).lower() in narrative.clean_text(r["report_text"]).lower(),
        axis=1)
    assert hits.mean() < 0.02, "narratives state their own crime head"


# ----------------------------------------------------------- reproducibility
def test_suspect_recommendation_is_stable_across_calls(data):
    """Regression test for a real bug.

    Match scores used `hash(suspect_id) % 5` as a tie-breaker. Python randomises
    string hashing per process, so a suspect's match score — the basis for naming
    them a person of interest — changed on every server restart.
    """
    import analytics

    cases = data["cases"]
    if cases.empty:
        pytest.skip("no cases")

    firs = cases.rename(columns={"ts": "timestamp"}).copy()
    firs["case_id"] = firs["case_id"].astype(str)
    suspects = data["suspects"]
    accused_raw = data["raw"]["Accused"]
    if suspects is None or suspects.empty or accused_raw.empty:
        pytest.skip("no suspect registry")

    cache = {"Accused": accused_raw, "criminals": data["offenders"].rename(
        columns={"offender_id": "criminal_id"}), "suspects": suspects}
    case_id = firs.iloc[0]["case_id"]

    first = analytics.recommend_suspects_for_case(case_id, firs, suspects, accused_raw, cache)
    second = analytics.recommend_suspects_for_case(case_id, firs, suspects, accused_raw, cache)
    assert [r["suspect_id"] for r in first] == [r["suspect_id"] for r in second]
    assert [r["match_score"] for r in first] == [r["match_score"] for r in second]


def test_no_match_score_depends_on_python_hash_randomisation():
    """Guard the source itself, since the symptom only shows across processes.

    Comments are stripped first — the fix is documented in a comment that names
    the old expression, and the guard is about executable code.
    """
    import io
    import tokenize

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "analytics.py")
    src = open(path, encoding="utf-8").read()
    code = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            code.append(tok.string)
    joined = "".join(code)
    assert "hash(s_id)" not in joined
    assert "hash(" not in joined.replace("hashlib", ""), \
        "built-in hash() is process-dependent; use a stable key for anything ranked"


# --------------------------------------------------------------- artifacts
def test_registry_report_is_complete_and_honest():
    report = registry.read_report()
    if report is None:
        pytest.skip("no training run recorded — run `python -m ml.train`")

    assert report.get("data_provenance", {}).get("source"), "provenance not recorded"
    assert report.get("evaluation_policy"), "evaluation policy not recorded"

    for name, entry in (report.get("models") or {}).items():
        assert entry.get("algorithm"), f"{name}: no algorithm recorded"
        assert entry.get("split"), f"{name}: no split recorded"
        assert entry.get("headline"), f"{name}: no headline"
        # Unsupervised models have no baseline to beat; supervised ones must.
        if entry.get("task", "").startswith("unsupervised"):
            continue
        assert entry.get("baselines"), f"{name}: reported without any baseline"


def test_every_supervised_model_is_split_on_time():
    report = registry.read_report()
    if report is None:
        pytest.skip("no training run recorded")
    for name, entry in (report.get("models") or {}).items():
        strategy = (entry.get("split") or {}).get("strategy", "")
        if entry.get("task", "").startswith("unsupervised"):
            continue
        assert any(k in strategy.lower() for k in ("temporal", "walk-forward", "panel")), \
            f"{name} is not split on time: {strategy!r}"


@pytest.mark.parametrize("name", ["narrative_classifier", "recidivism_risk",
                                  "conviction_outcome", "case_closure"])
def test_persisted_artifact_produces_a_working_predictor(name):
    artifact = registry.load_artifact(name)
    if artifact is None:
        pytest.skip(f"{name} artifact not present or built by another sklearn")
    from ml.tasks import TASKS
    predict = TASKS[name].make_predictor(artifact)
    assert callable(predict)


def test_classifier_predicts_a_known_class():
    artifact = registry.load_artifact("narrative_classifier")
    if artifact is None:
        pytest.skip("classifier artifact unavailable")
    predict = narrative.make_predictor(artifact)
    out = predict("The complainant received a call from an unknown number claiming to be "
                  "from a bank. A one-time password was shared and the amount was debited.")
    assert out and 0.0 <= out[0]["prob"] <= 1.0
    assert out[0]["label"] in artifact["classes"]


def test_classifier_returns_nothing_for_empty_input():
    artifact = registry.load_artifact("narrative_classifier")
    if artifact is None:
        pytest.skip("classifier artifact unavailable")
    predict = narrative.make_predictor(artifact)
    assert predict("") == []
    assert predict(None) == []


def test_hotspot_zones_are_small_enough_to_patrol():
    artifact = registry.load_artifact("hotspot_clusters")
    if artifact is None:
        pytest.skip("hotspot artifact unavailable")
    zones = artifact["zones"]
    assert zones
    radii = [z["radius_km"] for z in zones]
    assert np.median(radii) <= hotspots.MAX_USEFUL_RADIUS_KM, \
        "the median 'hotspot' is too large to send a unit to"


def test_recidivism_scores_are_never_stated_as_certainty():
    """No model on this much data can justify a displayed 0% or 100%."""
    artifact = registry.load_artifact("recidivism_risk")
    if artifact is None:
        pytest.skip("recidivism artifact unavailable")
    data_bundle = dataset.load_canonical()
    out = recidivism.score_current_offenders(data_bundle, artifact, top_n=25)
    if not out.get("top"):
        pytest.skip("no offenders scored")
    for row in out["top"]:
        assert 0 < row["reoffence_pct"] < 100
        assert 0 <= row["risk_index"] <= 100


# ------------------------------------------------------------ survival: censoring
def test_censored_rows_are_never_scored_as_failures():
    """The whole point of the module. A censored case contributes at-risk
    intervals and fails in none of them."""
    _, _, y = survival.expand_person_period([74.0], [0], interval_days=30, n_intervals=12)
    assert len(y) == 3, "74 days spans three 30-day intervals"
    assert y.sum() == 0, "a censored case must never produce an event row"


def test_event_row_fires_exactly_once_in_the_right_interval():
    idx, interval, y = survival.expand_person_period(
        [74.0], [1], interval_days=30, n_intervals=12)
    assert list(interval) == [1, 2, 3]
    assert list(y) == [0, 0, 1], "the event belongs in the interval containing day 74"
    assert y.sum() == 1


def test_event_beyond_the_grid_is_censored_not_recorded_late():
    """An event at day 400 is not observable on a 360-day grid. Recording it as a
    failure inside the grid would be inventing information."""
    _, interval, y = survival.expand_person_period(
        [400.0], [1], interval_days=30, n_intervals=12)
    assert interval.max() == 12, "follow-up is truncated at the grid edge"
    assert y.sum() == 0, "an out-of-grid event must be treated as censored"


def test_same_day_event_still_produces_a_row():
    _, interval, y = survival.expand_person_period([0.5], [1], 30, 12)
    assert len(interval) == 1 and y.sum() == 1


# ------------------------------------------------------------ survival: Kaplan-Meier
def test_kaplan_meier_is_monotone_non_increasing():
    km = survival.kaplan_meier([5, 10, 15, 20, 25], [1, 0, 1, 1, 0])
    s = km["survival"]
    assert all(s[i] >= s[i + 1] - 1e-12 for i in range(len(s) - 1))
    assert s[0] <= 1.0


def test_kaplan_meier_differs_from_a_naive_survivor_fraction():
    """The reason KM exists. With durations 10/20/30 and events 1/0/1, the last
    observed failure empties the risk set, so S drops to 0 — whereas a naive
    'fraction not yet failed' would report 1/3 and overstate survival."""
    km = survival.kaplan_meier([10, 20, 30], [1, 0, 1])
    assert km["survival"][0] == pytest.approx(2 / 3)
    assert km["survival"][-1] == pytest.approx(0.0)
    naive = 1 - (2 / 3)  # fraction that visibly failed
    assert km["survival"][-1] != pytest.approx(naive)


def test_km_median_returns_none_when_the_curve_never_reaches_half():
    """Better to say 'unknown' than to invent a median from the failures only."""
    assert survival.km_median([100, 200, 300], [0, 0, 0]) is None


# ------------------------------------------------------------ survival: concordance
def test_concordance_is_one_for_a_perfect_ranking():
    d = [1, 2, 3, 4]
    e = [1, 1, 1, 1]
    risk = [4, 3, 2, 1]          # shorter survival <=> higher risk
    assert survival.concordance_index(d, e, risk) == pytest.approx(1.0)


def test_concordance_is_zero_for_an_inverted_ranking():
    assert survival.concordance_index([1, 2, 3, 4], [1, 1, 1, 1],
                                      [1, 2, 3, 4]) == pytest.approx(0.0)


def test_concordance_ignores_pairs_it_cannot_order():
    """If the shorter-lived row is censored we do not know who failed first, and
    guessing would manufacture skill the model does not have."""
    # Only row 0 has an event, so the sole comparable pair is (0, 1).
    c = survival.concordance_index([1, 2], [1, 0], [10, 1])
    assert c == pytest.approx(1.0)
    # With no events at all there is nothing comparable to score.
    assert survival.concordance_index([1, 2], [0, 0], [10, 1]) is None


def test_survival_curve_from_hazards_is_monotone():
    hz = np.full((3, 6), 0.2)
    s = survival.survival_from_hazard(hz)
    assert s.shape == (3, 6)
    assert np.all(np.diff(s, axis=1) <= 1e-12)
    assert s[0, 0] == pytest.approx(0.8)


def test_horizon_labels_drop_rows_that_cannot_answer_the_question():
    """A case censored at day 40 says nothing about day 90 and must be excluded
    rather than counted as a non-event."""
    mask, y = survival.horizon_labels([40, 100, 30], [0, 0, 1], 90)
    assert list(mask) == [False, True, True], "the day-40 censored row is unanswerable"
    assert list(y) == [0, 1]


# ------------------------------------------------------------ pendency model
def test_pendency_uses_the_cases_the_binary_model_discards(data):
    """The core claim: modelling censoring recovers the recent docket."""
    surv_frame = pendency.build_frame(data)
    binary_frame = allocation.build_frame(data)
    assert len(surv_frame) > len(binary_frame), (
        "the survival frame must retain cases too recent for the 180-day binary target")


def test_pendency_does_not_mutate_the_allocation_window(data):
    """Regression: `pendency` used to reach the full case table by assigning
    `allocation.WINDOW_DAYS = 0` and restoring it in a `finally`.

    That is not reentrant. Two overlapping calls leave the global at 0 permanently,
    after which every case-closure frame silently includes cases that cannot have
    an observable outcome and the model trains on "chargesheet within 0 days". The
    app serves 8 threads and warms up in a background thread, so the interleaving
    is reachable in production — and it fails silently, which is why it surfaced as
    an unrelated test failing under a particular ordering rather than as an error.
    """
    before = allocation.WINDOW_DAYS
    pendency.build_frame(data)
    assert allocation.WINDOW_DAYS == before, "the module global was left modified"

    # And the same interleaving, run explicitly.
    full = allocation.build_frame(data, window_days=0)
    aged = allocation.build_frame(data)
    assert allocation.WINDOW_DAYS == before
    assert len(full) > len(aged), "window_days=0 must widen the frame, not narrow it"


def test_pendency_frame_marks_open_cases_as_censored_not_failed(data):
    frame = pendency.build_frame(data)
    assert set(frame["event"].unique()) <= {0, 1}
    assert (frame["duration_days"] > 0).all()
    open_rows = frame[frame["event"] == 0]
    assert len(open_rows) > 0
    assert open_rows["chargesheet_date"].isna().all(), (
        "a row marked censored must have no chargesheet date")


def test_pendency_inherits_the_banned_officer_features():
    """closure_rate and friends are aggregates of case outcomes. They stay out."""
    assert not (config.BANNED_ALLOCATION_FEATURES & set(pendency.FEATURES))


def test_pendency_beats_the_kaplan_meier_baseline():
    """A covariate model that cannot beat the covariate-free curve has learned
    nothing, and the registry has to show that it does."""
    report = registry.read_report()
    entry = (report.get("models") or {}).get("case_pendency")
    assert entry, "case_pendency missing from the registry"
    m = entry["metrics"]
    km = entry["baselines"][0]
    assert m["c_index"] > 0.5, "C-index must beat a coin flip"
    assert m["integrated_brier_score"] < km["integrated_brier_score"], (
        "the model must beat Kaplan-Meier on the proper scoring rule, not just on ranking")


def test_pendency_reports_its_own_censoring_rate():
    entry = (registry.read_report().get("models") or {})["case_pendency"]
    m = entry["metrics"]
    assert 0.0 < m["censoring_rate"] < 1.0
    assert m["n_test_censored"] > 0, (
        "a survival evaluation with no censored test rows is not testing censoring")


def test_pendency_per_horizon_metrics_publish_their_sample_size():
    """A figure computed on 12 rows must not look like one computed on 900."""
    entry = (registry.read_report().get("models") or {})["case_pendency"]
    for h in entry["metrics"]["per_horizon"]:
        assert h["n_evaluable"] >= 30
        assert 0.0 <= h["roc_auc"] <= 1.0


# ------------------------------------------------------------ docket refusal
@pytest.fixture(scope="module")
def docket(data):
    art = registry.load_artifact("case_pendency")
    if art is None:
        pytest.skip("case_pendency artifact unavailable in this runtime")
    return pendency.score_open_docket(data, art)


def test_docket_refuses_to_score_cases_past_the_model_grid(docket):
    """The refusal is the feature. Printing a probability against a six-year-old
    file launders an administrative fact as a model output."""
    grid_max = docket["summary"]["grid_max_days"]
    assert docket["backlog"]["count"] > 0, "this corpus does have a backlog to refuse"
    for c in docket["cases"]:
        assert c["days_open"] <= grid_max, (
            "a scored case must lie inside the model's observation grid")


def test_docket_counts_reconcile(docket):
    s = docket["summary"]
    assert s["forecastable"] + s["backlog"] == s["open_cases"]


def test_docket_marks_elapsed_horizons_as_fact_not_forecast(docket):
    """A horizon that has already passed is observed, and must be reported as
    certainty rather than as a prediction."""
    for c in docket["cases"]:
        for key, elapsed in c["horizon_elapsed"].items():
            if elapsed:
                assert c["breach_risk"][key] == 1.0
                assert c["days_open"] >= int(key.rstrip("d"))


def test_docket_ranking_actually_discriminates(docket):
    """Ranking on breach probability saturates at 1.0 and is useless for triage;
    expected remaining days has to spread across the docket."""
    vals = [c["expected_additional_days"] for c in docket["cases"]]
    assert len(set(round(v) for v in vals)) > 10, "ranking signal is degenerate"
    assert max(vals) - min(vals) > 30


def test_docket_conditional_risk_is_a_probability(docket):
    for c in docket["cases"]:
        for v in c["breach_risk"].values():
            assert 0.0 <= v <= 1.0


def test_docket_never_claims_certainty_about_a_future_horizon(docket):
    """Symmetry with the recidivism guard: no 0% or 100% about something that has
    not happened yet."""
    for c in docket["cases"]:
        for key, elapsed in c["horizon_elapsed"].items():
            if not elapsed:
                assert c["breach_risk"][key] < 1.0, (
                    f"case {c['case_id']} quotes certainty about an unelapsed horizon")


def test_survival_at_interpolates_inside_an_interval():
    """Constant hazard within an interval: S(15) must be the geometric midpoint
    of S(0) and S(30), not snapped to either boundary."""
    h = np.full((1, 12), 0.2)
    s15 = survival.survival_at(h, [15], 30)[0]
    s30 = survival.survival_at(h, [30], 30)[0]
    assert s30 == pytest.approx(0.8)
    assert s15 == pytest.approx(0.8 ** 0.5, rel=1e-6)
    assert s30 < s15 < 1.0


def test_survival_at_is_monotone_and_clamped_past_the_grid():
    h = np.full((1, 6), 0.15)
    days = [0, 10, 50, 120, 180, 400]
    vals = [survival.survival_at(h, [d], 30)[0] for d in days]
    assert all(vals[i] >= vals[i + 1] - 1e-12 for i in range(len(vals) - 1))
    assert vals[-1] == pytest.approx(vals[-2]), "past the grid edge S must flatten, not extrapolate"


# ------------------------------------------------------------ access control
def test_token_survives_a_round_trip():
    token = auth.make_token("sp", "supervisor")
    payload = auth.verify_token(token)
    assert payload and payload["u"] == "sp" and payload["r"] == "supervisor"


def test_a_tampered_token_is_rejected():
    """The signature is the only thing standing between a caller and the
    watchlist, so a token whose payload has been edited must not verify."""
    import base64
    import json as _json

    token = auth.make_token("analyst", "analyst")
    body, sig = token.rsplit(".", 1)
    forged_payload = _json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    forged_payload["r"] = "supervisor"
    forged_body = base64.urlsafe_b64encode(
        _json.dumps(forged_payload).encode()).decode().rstrip("=")
    assert auth.verify_token(f"{forged_body}.{sig}") is None


def test_expired_tokens_are_rejected(monkeypatch):
    token = auth.make_token("sp", "supervisor")
    monkeypatch.setattr(auth.time, "time", lambda: 9_999_999_999)
    assert auth.verify_token(token) is None


def test_unknown_role_in_a_token_is_rejected():
    assert auth.verify_token(auth.make_token("x", "root")) is None


def test_wrong_password_does_not_authenticate():
    # The second value is now a failure reason rather than None, so a lockout can be
    # told from a bad password and reported with the right status code.
    token, reason = auth.authenticate("sp", "not-the-password")
    assert token is None
    assert reason == "invalid_credentials"
    auth._clear_failures("sp")


def test_roles_are_ordered():
    assert auth.rank("supervisor") > auth.rank("investigator") > auth.rank("analyst")
    assert auth.rank("nonsense") == 0, "an unrecognised role must not outrank analyst"


# ------------------------------------------------------------ redaction
FULL_ROW = {
    "criminal_id": "SUS-8286",
    "name": "Deepa Murthy",
    "risk_index": 100.0,
    "probability": 0.9992,
    "reoffence_pct": 99.9,
    "band": "Extreme",
    "past_fir_count": 44,
    "days_since_last": 73,
    "active_warrant": True,
    "gang_affiliation": "Bawaria Gang",
    "drivers": ["44 prior cases on record", "operates across 20 districts"],
}


def test_redaction_removes_every_person_level_field():
    """Guards the bug this allowlist replaced: a denylist version leaked the real
    criminal_id, the probability under its second name reoffence_pct, and the
    free-text drivers that describe a person closely enough to re-identify."""
    out = auth.redact_watchlist_row(FULL_ROW)
    allowed = auth.WATCHLIST_PUBLIC_FIELDS | {"criminal_id", "name", "redacted"}
    assert set(out) <= allowed, f"redaction leaked: {set(out) - allowed}"

    blob = json.dumps(out)
    for secret in ("Deepa", "SUS-8286", "0.9992", "99.9", "Bawaria", "44 prior"):
        assert secret not in blob, f"redacted row still contains {secret!r}"


def test_redaction_is_not_a_denylist():
    """A field nobody anticipated must be dropped by default, not retained."""
    row = dict(FULL_ROW, aadhaar_last4="7391", home_address="12 MG Road")
    out = auth.redact_watchlist_row(row)
    assert "aadhaar_last4" not in out and "home_address" not in out


def test_redaction_keeps_the_distribution_legible():
    """Redaction must not empty the response — the watchlist should still read as
    a distribution, otherwise the signed-out view is useless rather than safe."""
    out = auth.redact_watchlist_row(FULL_ROW)
    assert out["band"] == "Extreme"
    assert out["risk_index"] == 100.0
    assert out["redacted"] is True


def test_pseudonyms_distinguish_people_but_do_not_identify_them():
    """The first version keyed off a field name that did not exist, so every row
    hashed None and the whole watchlist collapsed to one pseudonym."""
    rows = [dict(FULL_ROW, criminal_id=f"SUS-{i}") for i in range(20)]
    ids = [auth.redact_watchlist_row(r)["criminal_id"] for r in rows]
    assert len(set(ids)) == 20, "distinct people must get distinct pseudonyms"
    assert all(i.startswith("PSN-") for i in ids)
    assert all("SUS-" not in i for i in ids)
    # Stable within a process, so a table does not reshuffle between requests.
    assert auth.pseudonym("SUS-1") == auth.pseudonym("SUS-1")


def test_missing_identifier_still_redacts():
    out = auth.redact_watchlist_row({"band": "High", "name": "Someone"})
    assert out["name"].startswith("PSN-") and "Someone" not in json.dumps(out)


# ------------------------------------------------------- stale-artifact detection
def test_corpus_fingerprint_is_stable_and_cheap():
    a = registry.data_fingerprint()
    b = registry.data_fingerprint()
    assert a and a == b, "fingerprint must be deterministic for an unchanged corpus"
    assert a["files"] > 10 and len(a["sha256"]) == 16


CSV_ONE_ROW = "a,b" + chr(10) + "1,2" + chr(10)
CSV_TWO_ROWS = "a,b" + chr(10) + "1,2" + chr(10) + "3,4" + chr(10)


def test_fingerprint_changes_when_a_table_changes(tmp_path):
    """A regenerated corpus must produce a different signature, or a stale artifact
    goes undetected."""
    d = tmp_path / "data"
    d.mkdir()
    (d / "CaseMaster.csv").write_text(CSV_ONE_ROW, encoding="utf-8")
    first = registry.data_fingerprint(str(d))
    (d / "CaseMaster.csv").write_text(CSV_TWO_ROWS, encoding="utf-8")
    second = registry.data_fingerprint(str(d))
    assert first and second and first["sha256"] != second["sha256"]


def test_missing_directory_does_not_produce_a_false_mismatch(tmp_path):
    """Being unable to check is not the same as a mismatch: a None fingerprint must
    never cause an artifact to be refused."""
    assert registry.data_fingerprint(str(tmp_path / "nope")) is None


def test_an_artifact_from_another_corpus_is_refused(tmp_path, monkeypatch):
    """The failure this guards: a .joblib trained on data that no longer exists
    loads happily, scores, and is published beside metrics describing a corpus the
    system is not running on."""
    joblib = pytest.importorskip("joblib")
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))

    payload = {"name": "probe", "estimator": None,
               "_env": registry._env_stamp(),
               # Must carry the CURRENT scheme version: a stamp from an older scheme
               # is deliberately treated as unstamped rather than as a mismatch, so
               # omitting it here would exercise the wrong branch.
               "_data_fingerprint": {"version": registry.FINGERPRINT_VERSION,
                                     "files": 38, "sha256": "0000deadbeef0000"}}
    joblib.dump(payload, registry.artifact_path("probe"), compress=3)

    assert registry.load_artifact("probe") is None, (
        "an artifact trained on a different corpus was accepted")


def test_a_matching_artifact_still_loads(tmp_path, monkeypatch):
    joblib = pytest.importorskip("joblib")
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))

    payload = {"name": "probe", "estimator": None,
               "_env": registry._env_stamp(),
               "_data_fingerprint": registry.data_fingerprint()}
    joblib.dump(payload, registry.artifact_path("probe"), compress=3)

    assert registry.load_artifact("probe") is not None


def test_an_unstamped_artifact_is_still_accepted(tmp_path, monkeypatch):
    """Backward compatible: artifacts predating the fingerprint must not be refused,
    or every existing deployment retrains on the next boot."""
    joblib = pytest.importorskip("joblib")
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))

    joblib.dump({"name": "probe", "estimator": None, "_env": registry._env_stamp()},
                registry.artifact_path("probe"), compress=3)
    assert registry.load_artifact("probe") is not None


def test_fingerprint_ignores_mtime(tmp_path):
    """Regression: the fingerprint hashed name + size + mtime. `git clone`,
    `docker build` and a redeploy all rewrite mtimes without changing a byte, so
    every artifact would be refused on the next boot and all seven models would
    retrain in-process — in a container that cannot absorb it. A guard that fires on
    every deploy gets switched off."""
    import os

    d = tmp_path / "data"
    d.mkdir()
    f = d / "CaseMaster.csv"
    f.write_text(CSV_ONE_ROW, encoding="utf-8")
    first = registry.data_fingerprint(str(d))

    st = os.stat(f)
    os.utime(f, (st.st_atime + 10_000, st.st_mtime + 10_000))
    assert registry.data_fingerprint(str(d))["sha256"] == first["sha256"], (
        "an mtime change invalidated the fingerprint")


def test_fingerprint_detects_a_same_size_edit(tmp_path):
    """The other direction: an edit preserving byte size used to hash identically,
    so the stale artifact the check exists to catch slipped through."""
    d = tmp_path / "data"
    d.mkdir()
    f = d / "CaseMaster.csv"
    f.write_text("a,b" + chr(10) + "1,2" + chr(10), encoding="utf-8")
    first = registry.data_fingerprint(str(d))
    f.write_text("a,b" + chr(10) + "9,8" + chr(10), encoding="utf-8")   # same length
    assert registry.data_fingerprint(str(d))["sha256"] != first["sha256"]


def test_an_older_fingerprint_scheme_is_not_a_mismatch(tmp_path, monkeypatch):
    """A stamp from a previous algorithm is not evidence of a changed corpus, so it
    must be treated as unstamped rather than forcing a retrain."""
    joblib = pytest.importorskip("joblib")
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))
    joblib.dump({"name": "probe", "estimator": None,
                 "_env": registry._env_stamp(),
                 "_data_fingerprint": {"version": 1, "files": 38, "sha256": "old"}},
                registry.artifact_path("probe"), compress=3)
    assert registry.load_artifact("probe") is not None


# ------------------------------------------------------------------ monitoring
def test_psi_is_zero_for_an_unchanged_distribution():
    shares = [0.1] * 10
    assert monitoring.psi(shares, shares) == pytest.approx(0.0, abs=1e-9)


def test_psi_grows_with_the_size_of_the_shift():
    ref = [0.25, 0.25, 0.25, 0.25]
    small = monitoring.psi(ref, [0.30, 0.25, 0.25, 0.20])
    large = monitoring.psi(ref, [0.70, 0.15, 0.10, 0.05])
    assert 0 < small < large


def test_psi_survives_an_empty_bucket():
    """An empty bucket makes the log term infinite. Without flooring, one empty
    bucket dominates the whole statistic and the measure becomes unusable."""
    value = monitoring.psi([0.5, 0.5], [1.0, 0.0])
    assert value is not None and np.isfinite(value)


@pytest.mark.parametrize("value,expected", [
    (0.02, "stable"), (0.15, "moderate"), (0.40, "significant"), (None, "unknown"),
])
def test_psi_bands(value, expected):
    assert monitoring.band(value) == expected


def test_a_reference_profile_captures_the_training_distribution():
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"x": rng.normal(0, 1, 600),
                          "k": rng.choice(list("abc"), 600)})
    profile = monitoring.build_profile(frame, numeric=["x"], categorical=["k"],
                                       scores=rng.random(600))
    assert profile["n"] == 600
    assert len(profile["numeric"]["x"]["shares"]) == 10
    # Shares are rounded to 6dp per bucket, so the sum carries up to one rounding
    # unit per bucket — 1e-5 is the honest tolerance, not 1e-6.
    assert sum(profile["numeric"]["x"]["shares"]) == pytest.approx(1.0, abs=1e-5)
    assert sum(profile["categorical"]["k"].values()) == pytest.approx(1.0, abs=1e-5)
    assert "score" in profile


def test_drift_is_stable_against_the_data_it_was_built_from():
    rng = np.random.default_rng(1)
    frame = pd.DataFrame({"x": rng.normal(0, 1, 800),
                          "k": rng.choice(list("abc"), 800)})
    profile = monitoring.build_profile(frame, numeric=["x"], categorical=["k"])
    report = monitoring.compare(profile, frame)
    assert report["verdict"] == "stable"
    assert report["worst_psi"] < monitoring.PSI_STABLE


def test_drift_detects_a_shifted_population():
    """The failure this exists for: a model still returning confident scores while
    being asked about a different population than the one it learned."""
    rng = np.random.default_rng(2)
    reference = pd.DataFrame({"x": rng.normal(0, 1, 800)})
    profile = monitoring.build_profile(reference, numeric=["x"])
    shifted = pd.DataFrame({"x": rng.normal(3, 1, 800)})
    report = monitoring.compare(profile, shifted)
    assert report["verdict"] == "significant"
    assert report["features"][0]["feature"] == "x"


def test_drift_reports_an_unseen_category():
    reference = pd.DataFrame({"k": ["a"] * 300 + ["b"] * 300})
    profile = monitoring.build_profile(reference, categorical=["k"])
    current = pd.DataFrame({"k": ["a"] * 200 + ["z"] * 400})
    report = monitoring.compare(profile, current)
    assert "z" in report["features"][0]["unseen_categories"]


def test_drift_says_so_when_there_is_no_reference():
    """No profile is 'cannot tell', which must not read as 'stable'."""
    report = monitoring.compare(None, pd.DataFrame({"x": [1, 2, 3]}))
    assert report["available"] is False
    assert "reference profile" in report["reason"]


def test_drift_report_states_what_it_cannot_show():
    rng = np.random.default_rng(3)
    frame = pd.DataFrame({"x": rng.normal(0, 1, 400)})
    report = monitoring.compare(monitoring.build_profile(frame, numeric=["x"]), frame)
    assert "does NOT show the model got worse" in report["interpretation"]


def test_predictions_are_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring, "PREDICTION_LOG", str(tmp_path / "p.jsonl"))
    monitoring.log_prediction("recidivism_risk", "SUS-1042", 0.81,
                              subject_type="offender")
    recent = monitoring.recent_predictions(model="recidivism_risk", limit=5)
    assert recent and recent[0]["subject_id"] == "SUS-1042"
    assert recent[0]["score"] == pytest.approx(0.81)
    assert (tmp_path / "p.jsonl").read_text(encoding="utf-8").strip()


# ------------------------------------------------------------------ versioning
def test_saving_keeps_the_previous_version(tmp_path, monkeypatch):
    """save_artifact used to overwrite in place, so a bad training run replaced the
    working model with no way back except retraining and hoping."""
    joblib = pytest.importorskip("joblib")
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))
    for tag in ("v1", "v2"):
        registry.save_artifact("probe", {"name": "probe", "estimator": tag,
                                         "_env": registry._env_stamp()})
    versions = registry.read_version_index("probe")
    assert len(versions) == 2
    assert joblib.load(registry.artifact_path("probe"))["estimator"] == "v2"


def test_rollback_steps_back_through_history(tmp_path, monkeypatch):
    joblib = pytest.importorskip("joblib")
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))
    for tag in ("v1", "v2", "v3"):
        registry.save_artifact("probe", {"name": "probe", "estimator": tag,
                                         "_env": registry._env_stamp()})

    def live():
        return joblib.load(registry.artifact_path("probe"))["estimator"]

    assert live() == "v3"
    registry.rollback("probe")
    assert live() == "v2", "rollback must step back, not toggle"
    registry.rollback("probe")
    assert live() == "v1"
    assert registry.rollback("probe") is None, "must refuse past the oldest kept"


def test_two_saves_in_one_second_do_not_collide(tmp_path, monkeypatch):
    """The version id is second-resolution, so a retrain-and-retry inside one second
    would otherwise overwrite the very version you want to roll back to."""
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))
    for tag in ("a", "b", "c"):
        registry.save_artifact("probe", {"name": "probe", "estimator": tag,
                                         "_env": registry._env_stamp()})
    ids = [v["id"] for v in registry.read_version_index("probe")]
    assert len(set(ids)) == 3, f"version ids collided: {ids}"


def test_old_versions_are_pruned(tmp_path, monkeypatch):
    """The container disk is small; an unbounded archive of estimators fills it."""
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))
    for i in range(registry.KEEP_VERSIONS + 3):
        registry.save_artifact("probe", {"name": "probe", "estimator": i,
                                         "_env": registry._env_stamp()})
    assert len(registry.read_version_index("probe")) == registry.KEEP_VERSIONS


def test_exactly_one_version_is_live(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARTIFACT_DIR", str(tmp_path))
    for tag in ("v1", "v2", "v3"):
        registry.save_artifact("probe", {"name": "probe", "estimator": tag,
                                         "_env": registry._env_stamp()})
    registry.rollback("probe")
    live = [v for v in registry.read_version_index("probe") if v["live"]]
    assert len(live) == 1


# ------------------------------------------------------------------ schema
def test_generated_schema_is_valid_ddl():
    """The previous generator guessed primary keys by substring, so three tables
    declared several and Postgres would reject the file outright. It had never been
    executed, which is why nobody noticed."""
    import sqlite3

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "schema.sql")
    if not os.path.exists(path):
        pytest.skip("schema.sql not generated")
    sql = open(path, encoding="utf-8").read()

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(sql)          # raises if the DDL is invalid
    tables = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")]
    assert len(tables) > 30
    assert not con.execute("PRAGMA foreign_key_check").fetchall()


def test_no_table_declares_two_primary_keys():
    import re

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "schema.sql")
    if not os.path.exists(path):
        pytest.skip("schema.sql not generated")
    sql = open(path, encoding="utf-8").read()
    offenders = [
        m.group(1) for m in re.finditer(
            r'CREATE TABLE IF NOT EXISTS "(\w+)" \((.*?)\n\);', sql, re.S)
        if len(re.findall(r"\bPRIMARY KEY\b", m.group(2))) > 1
    ]
    assert not offenders, f"multiple PRIMARY KEY declarations in: {offenders}"


# ------------------------------------------------------------------ db source
@pytest.fixture(scope="module")
def corpus_db(tmp_path_factory):
    """Build a real database from the generated schema and the CSV corpus."""
    import load_to_db

    if not os.path.exists(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")):
        pytest.skip("schema.sql not generated")
    path = str(tmp_path_factory.mktemp("db") / "corpus.db")
    load_to_db.build(path, verbose=False)
    return path


def test_the_corpus_satisfies_referential_integrity(corpus_db):
    """The check a flat file cannot be given.

    A dangling CaseMasterID in Accused is invisible in a CSV, and an inner join
    silently drops the row — which is how a model ends up trained on fewer records
    than anyone believes.
    """
    import sqlite3

    con = sqlite3.connect(corpus_db)
    con.execute("PRAGMA foreign_keys=ON")
    violations = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    assert not violations, f"{len(violations)} foreign keys do not resolve"


def test_both_sources_produce_identical_frames(corpus_db):
    """The claim this codebase rests on: `ml/dataset.py` is the only module that
    knows the physical schema, so swapping the source changes nothing downstream.

    It was never tested — every path ended at `pd.read_csv`, so "point it at a real
    extract" was an assertion. This makes it a measured property.
    """
    import hashlib

    from_csv = dataset.load_canonical()
    from_db = dataset.load_canonical(source=corpus_db)

    def fingerprint(df):
        ordered = df.sort_values(list(df.columns), kind="mergesort").astype(str)
        return hashlib.sha256(
            pd.util.hash_pandas_object(ordered, index=False).values.tobytes()
        ).hexdigest()

    differing = [
        key for key in ("cases", "reports", "links", "suspects", "officers",
                        "offenders", "accused", "courts", "arrests",
                        "chargesheets", "socio")
        if fingerprint(from_csv[key]) != fingerprint(from_db[key])
    ]
    assert not differing, f"the two sources disagree on: {differing}"


def test_sql_nulls_match_csv_missing_values(corpus_db):
    """SQL NULL arrives as None, read_csv produces NaN. Both are missing to
    `pd.isna`, but downstream code stringifies these and 'none' vs 'nan' is the kind
    of difference that hides in a magic tuple until it does not."""
    raw = dataset.load_raw_from_db(corpus_db)
    suspects = raw["suspects"]
    if "gang_affiliation" in suspects:
        rendered = set(suspects["gang_affiliation"].astype(str))
        assert "None" not in rendered, "a SQL NULL leaked through as the string 'None'"


def test_a_missing_table_is_reported_not_silently_empty(tmp_path):
    """A required table that is absent must raise. Returning an empty frame would
    train a model on nothing and report it as a normal run."""
    import sqlite3

    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()
    with pytest.raises(RuntimeError, match="Required table missing"):
        dataset.load_raw_from_db(path)
