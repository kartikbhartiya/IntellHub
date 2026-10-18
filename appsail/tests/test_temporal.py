"""Hour-by-weekday grid: counted right, and honest about whether it shows anything.

* **Counts are the corpus's.** Every cell is recomputed with pandas.
* **A planted pattern is found; an even spread is not.** "No pattern beyond chance"
  on the simulated corpus is only worth saying if the same code flags a real one.
* **The peak window wraps midnight.** Sunday 23:00 runs into Monday 00:00; a window
  that stops at the end of the array would miss the busiest late-night stretch.
"""
import os
import sys

import pandas as pd
import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import temporal as TM  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def loaded():
    main.load_and_build_intelligence()
    yield
    TM.configure(lambda: main.data_cache)


@pytest.fixture
def corpus():
    TM.configure(lambda: main.data_cache)
    return main.data_cache


def _fake(cells, district="Mysuru", crime="Chain Snatching"):
    """cells: {(weekday, hour): n}; 2024-01-01 is a Monday."""
    rows = []
    for (d, h), n in cells.items():
        for k in range(n):
            ts = pd.Timestamp(2024, 1, 1) + pd.Timedelta(days=d + 7 * (k % 50), hours=h, minutes=k % 60)
            rows.append({"district": district, "crime_type": crime, "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S")})
    return {"firs": pd.DataFrame(rows)}


def test_cells_match_pandas(corpus):
    body, status = TM.temporal("Bengaluru Urban")
    assert status == 200
    firs = corpus["firs"]
    ts = pd.to_datetime(firs["timestamp"], errors="coerce")
    scope = firs["district"] == "Bengaluru Urban"
    for d, h in ((0, 0), (4, 21), (6, 23)):
        expected = int((scope & (ts.dt.dayofweek == d) & (ts.dt.hour == h)).sum())
        assert body["grid"][d][h] == expected
    assert body["firs"] == int(scope.sum())
    assert sum(r["count"] for r in body["by_hour"]) == body["firs"]


def test_top_crime_is_the_cells_mode(corpus):
    body, _ = TM.temporal()
    firs = corpus["firs"]
    ts = pd.to_datetime(firs["timestamp"], errors="coerce")
    cell = firs[(ts.dt.dayofweek == 4) & (ts.dt.hour == 20)]
    counts = cell["crime_type"].value_counts()
    top = body["top_crime"][4][20]
    assert top["count"] == int(counts.iloc[0]) and counts[top["crime_type"]] == counts.iloc[0]


def test_the_simulated_corpus_is_reported_as_patternless(corpus):
    body, _ = TM.temporal()
    assert body["tests"]["cells"]["pattern"] is False
    assert "No time pattern beyond chance" in body["note"]
    assert body["peak_cell"]["count"] == body["max_cell"]   # the peak is still reported


def test_a_planted_pattern_is_found():
    cells = {(d, h): 20 for d in range(7) for h in range(24)}
    cells[(4, 21)] = 120
    cells[(4, 22)] = 110
    TM.configure(lambda: _fake(cells))
    body, _ = TM.temporal()
    assert body["tests"]["cells"]["pattern"] is True
    assert {c["label"] for c in body["hot_cells"]} >= {"Fri 21:00–22:00", "Fri 22:00–23:00"}
    assert body["peak_window"]["weekday"] == "Friday" and body["peak_window"]["start_hour"] in (20, 21)
    assert "beyond chance" in body["note"] and "No time pattern" not in body["note"]


def test_an_even_spread_has_no_hot_cells():
    TM.configure(lambda: _fake({(d, h): 10 for d in range(7) for h in range(24)}))
    body, _ = TM.temporal()
    assert body["tests"]["cells"]["statistic"] == 0 and not body["hot_cells"]


def test_the_peak_window_wraps_past_midnight():
    cells = {(d, h): 1 for d in range(7) for h in range(24)}
    cells[(6, 23)] = 40
    cells[(0, 0)] = 40
    cells[(0, 1)] = 40
    TM.configure(lambda: _fake(cells))
    w = TM.temporal()[0]["peak_window"]
    assert w["label"] == "Sunday 23:00–02:00" and w["count"] == 120


def test_a_thin_scope_says_it_cannot_carry_a_pattern():
    TM.configure(lambda: _fake({(1, 9): 3, (2, 14): 2}))
    assert "too few" in TM.temporal()[0]["note"]


def test_filters_are_validated_and_case_insensitive(corpus):
    assert TM.temporal("atlantis")[1] == 400
    assert TM.temporal(None, "piracy")[1] == 400
    assert TM.temporal("mysuru")[0]["filters"]["district"] == "Mysuru"


def test_route(corpus):
    app = Flask("tm-test")
    app.register_blueprint(TM.bp)
    with app.test_client() as c:
        r = c.get("/serverless/api/analytics/temporal?district=Mysuru&crime_type=Murder")
        assert r.status_code == 200 and r.get_json()["filters"] == {"district": "Mysuru", "crime_type": "Murder"}
        assert c.get("/serverless/api/analytics/temporal?district=Nowhere").status_code == 400
