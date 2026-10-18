"""The annual forecaster: out of sample, counted right, and able to find a trend.

What these pin down, and why each one matters on the card:

* **A backtest year is out of sample.** "The model would have said 204 for 2025" is
  only worth reading if 2025 never reached the fit. Corrupting every 2025 row must
  leave the 2025 prediction untouched and move only the actual.
* **History is the corpus's.** Each count is recomputed here with pandas; a join that
  drops or duplicates rows fails.
* **It is not a flat line by construction.** On this corpus the model forecasts a
  level, because the corpus has no trend. On a planted 10%-a-year series it must find
  the trend and forecast above the last year — otherwise "no trend survived" would be
  a property of the code, not of the data.
* **Bad input is refused, not guessed.** An unknown district or a year with too
  little history answers 400 with a reason.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import karnataka  # noqa: E402
import main  # noqa: E402
import trend_forecast as T  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def loaded():
    main.load_and_build_intelligence()
    T.configure(lambda: main.data_cache)
    yield
    T.configure(lambda: main.data_cache)


@pytest.fixture
def corpus():
    T.configure(lambda: main.data_cache)
    return main.data_cache


def _years(firs):
    return pd.to_datetime(firs["timestamp"], errors="coerce").dt.year


def _synthetic(growth=1.10, start=2018, end=2025, end_date=None, seed=7):
    """A firs-shaped frame with population-weighted volumes growing `growth` a year."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in karnataka.DISTRICT_NAMES:
        base = 4.0 * karnataka.population(d)
        for y in range(start, end + 1):
            n = int(rng.poisson(base * growth ** (y - start)))
            last = pd.Timestamp(end_date) if (end_date and y == end) else pd.Timestamp(y, 12, 31)
            span = (last - pd.Timestamp(y, 1, 1)).days
            for k in range(n):
                day = pd.Timestamp(y, 1, 1) + pd.Timedelta(days=int(k * span / max(n, 1)))
                rows.append({"district": d, "crime_type": "Chain Snatching" if k % 2 else "Murder",
                             "timestamp": day.strftime("%Y-%m-%d 10:00:00")})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ out of sample
def test_a_backtest_year_never_reaches_the_fit(corpus):
    meta = T.meta()
    year = meta["years"]["backtest_years"][-1]
    before = T.forecast("Belagavi", year)
    assert before["kind"] == "backtest" and max(before["trained_on"]["years"]) == year - 1

    firs = corpus["firs"]
    target = firs[_years(firs) == year]
    tampered = {"firs": pd.concat([firs, target, target], ignore_index=True)}
    T.configure(lambda: tampered)
    after = T.forecast("Belagavi", year)
    assert after["predicted"] == before["predicted"], "the held-out year leaked into the fit"
    assert after["actual"] == 3 * before["actual"]


def test_the_fit_for_a_forecast_year_uses_every_complete_year(corpus):
    meta = T.meta()
    f = T.forecast("Mysuru", meta["years"]["forecast_years"][0])
    assert f["kind"] == "forecast" and f["trained_on"]["years"] == meta["years"]["data_years"]


# ------------------------------------------------------------------ counts
def test_history_counts_match_pandas(corpus):
    firs = corpus["firs"]
    ys = _years(firs)
    f = T.forecast("Bengaluru Urban", crime_type="Chain Snatching")
    for row in f["history"]:
        expected = int(((firs["district"] == "Bengaluru Urban") & (firs["crime_type"] == "Chain Snatching")
                        & (ys == row["year"])).sum())
        assert row["count"] == expected, row


def test_the_state_is_the_sum_of_its_districts(corpus):
    year = T.meta()["years"]["default"]
    state = T.forecast("Karnataka", year)
    outlook = T.outlook(year)
    total = sum(r["predicted"] for r in outlook["districts"])
    assert abs(state["predicted"] - total) <= len(outlook["districts"]) / 2 + 1
    assert state["history"][-1]["count"] == outlook["state"]["last_count"]


def test_every_interval_brackets_its_forecast(corpus):
    for r in T.outlook()["districts"]:
        assert r["interval"]["low"] <= r["predicted"] <= r["interval"]["high"], r
    assert T.forecast("Karnataka")["interval"]["low"] < T.forecast("Karnataka")["interval"]["high"]


def test_breakdown_covers_every_crime_type_once(corpus):
    f = T.forecast("Mysuru")
    names = [r["crime_type"] for r in f["by_crime_type"]]
    assert sorted(names) == sorted(T.meta()["crime_types"]) and len(set(names)) == len(names)


# ------------------------------------------------------------------ the model card
def test_the_model_card_is_out_of_sample_and_against_baselines(corpus):
    card = T.meta()["model"]
    h1 = card["backtest"]["1"]
    assert {b["strategy"] for b in h1["baselines"]} == set(T.BASELINES)
    last = next(b for b in h1["baselines"] if b["strategy"] == "last_year")
    assert h1["skill_vs_last_year"] == round(1 - h1["model"]["mae"] / last["mae"], 3)
    assert h1["significance_vs_best_baseline"]["verdict"] in ("better", "worse", "indistinguishable")
    assert 0.0 <= h1["interval"]["realised_coverage"] <= 1.0
    assert h1["interval"]["coverage_points"] == h1["points"]
    assert min(h1["target_years"]) >= card["backtest"]["1"]["target_years"][0]
    assert all("_" != k[0] for k in h1)          # internals never reach the response
    assert card["finding"]


def test_the_model_beats_last_years_count_on_this_corpus(corpus):
    # Not a target the code was tuned to — the penalties are cross-validated inside
    # each training window — but a model that loses to "same as last year" has no
    # business on the analytics page, so it is pinned.
    h1 = T.meta()["model"]["backtest"]["1"]
    assert h1["skill_vs_last_year"] > 0


# ------------------------------------------------------------------ it can find a trend
def test_a_planted_trend_is_found():
    fake = {"firs": _synthetic(growth=1.10)}
    T.configure(lambda: fake)
    f = T.forecast("Belagavi", 2026)
    assert f["trend"]["combined_pct_per_year"] > 5.0, f["trend"]
    assert f["predicted"] > f["last_count"]
    state = T.forecast("Karnataka", 2026)
    assert state["change_vs_last_pct"] > 5.0


def test_a_flat_series_is_forecast_flat():
    fake = {"firs": _synthetic(growth=1.0)}
    T.configure(lambda: fake)
    f = T.forecast("Karnataka", 2026)
    assert abs(f["trend"]["combined_pct_per_year"]) < 2.0


def test_a_part_year_is_left_out_of_training():
    fake = {"firs": _synthetic(growth=1.0, end=2025, end_date="2025-06-30")}
    T.configure(lambda: fake)
    meta = T.meta()
    assert meta["years"]["partial_years"] == [2025]
    assert meta["years"]["data_years"][-1] == 2024
    assert "2025" in meta["note"]


def test_new_firs_invalidate_the_fitted_models():
    a = {"firs": _synthetic(growth=1.0)}
    T.configure(lambda: a)
    first = T.forecast("Kodagu", 2026)["predicted"]
    extra = _synthetic(growth=1.0, seed=99)
    extra = extra[extra["district"] == "Kodagu"]
    b = {"firs": pd.concat([a["firs"], extra, extra], ignore_index=True)}
    T.configure(lambda: b)
    assert T.forecast("Kodagu", 2026)["predicted"] > first


# ------------------------------------------------------------------ refusals
@pytest.mark.parametrize("kw,needle", [
    ({"unit": "Atlantis"}, "Unknown unit"),
    ({"crime_type": "Piracy"}, "Unknown crime type"),
    ({"year": "1999"}, "outside"),
    ({"year": "next"}, "number"),
])
def test_bad_input_is_refused_with_a_reason(corpus, kw, needle):
    with pytest.raises(T.ForecastError, match=needle):
        T.forecast(**kw)


def test_routes_answer_and_refuse(corpus):
    app = Flask("tf-test")
    app.register_blueprint(T.bp)
    with app.test_client() as c:
        ok = c.get("/serverless/api/forecast/annual?unit=Mysuru")
        assert ok.status_code == 200 and ok.get_json()["unit"] == "Mysuru"
        assert c.get("/serverless/api/forecast/annual/meta").status_code == 200
        assert c.get("/serverless/api/forecast/annual/outlook").status_code == 200
        bad = c.get("/serverless/api/forecast/annual?unit=Atlantis")
        assert bad.status_code == 400 and "Unknown unit" in bad.get_json()["error"]


def test_unit_names_are_case_insensitive(corpus):
    assert T.forecast("mysuru")["unit"] == "Mysuru"
    assert T.forecast("statewide")["unit"] == "Karnataka"
