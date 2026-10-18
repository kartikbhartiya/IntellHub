"""When crime happens: FIRs by hour of day and day of week.

* ``GET /serverless/api/analytics/temporal?district=&crime_type=`` — a 7 x 24 grid
  of FIR counts (Monday first, hours 00–23 of ``IncidentFromDate``), the most common
  crime type in each cell, the busiest day, hour and three-hour window, and a test of
  whether any of it is more than chance.

Why the test is in the response
-------------------------------
A heatmap always shows a hottest cell. On 168 cells of a few dozen FIRs each, the
hottest cell is mostly the luck of the draw, and a bright square at "Friday 21:00"
reads as a deployment recommendation whether or not it is one. So the response
carries a chi-square test of the grid against an even spread (and of hours and
weekdays separately, Holm-corrected across the three), and marks a cell *hot* only when its standardised residual
clears a Bonferroni-adjusted threshold across all 168 cells. The peak is always
reported — it is a fact about the records — but the note says whether it is a
pattern.

Honest about the corpus
-----------------------
The FIR corpus is simulated, and its incident times are drawn close to uniformly
over hours and weekdays: "House Break-in (Night)" occurs at noon about as often as at
midnight. On the full corpus the test does not find a pattern, and the note says so.
FIRs registered through the platform carry real times and enter the same grid.

Read only through the getter given to ``configure``; main.py rebinds tables on reload.
"""
import math
import threading

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, request

bp = Blueprint("temporal", __name__)
API = "/serverless/api"

_cache_getter = lambda: {}   # noqa: E731 — replaced by configure()


def configure(cache_getter):
    """Bind the live data cache. A callable, never the dict itself."""
    global _cache_getter
    _cache_getter = cache_getter


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
ALPHA = 0.05
WINDOW_HOURS = 3
# Below this many FIRs the grid is still drawn but the note says it cannot carry a
# pattern: 168 cells need several FIRs each before any of them can stand out.
MIN_FOR_PATTERN = 5 * 168

_lock = threading.Lock()
_memo = {"key": None, "frame": None}


def _frame():
    cache = _cache_getter() or {}
    firs = cache.get("firs")
    if firs is None or len(firs) == 0:
        return None
    key = (id(firs), len(firs))
    with _lock:
        if _memo["key"] == key:
            return _memo["frame"]
    ts = pd.to_datetime(firs["timestamp"], errors="coerce")
    f = pd.DataFrame({
        "district": firs["district"].astype(str),
        "crime_type": firs["crime_type"].astype(str),
        "weekday": ts.dt.dayofweek,
        "hour": ts.dt.hour,
    })
    f = f.dropna(subset=["weekday", "hour"])
    f["weekday"] = f["weekday"].astype(int)
    f["hour"] = f["hour"].astype(int)
    with _lock:
        _memo.update(key=key, frame=f)
    return f


def _chi2_sf(stat, df):
    """Upper-tail p-value of a chi-square statistic."""
    try:
        from scipy.stats import chi2
        return float(chi2.sf(stat, df))
    except Exception:   # Wilson–Hilferty: within a few percent for df > 10
        z = ((stat / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
        return float(0.5 * math.erfc(z / math.sqrt(2)))


def _z_crit(alpha):
    """One-sided standard-normal critical value."""
    try:
        from scipy.stats import norm
        return float(norm.isf(alpha))
    except Exception:
        lo, hi = 0.0, 10.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if 0.5 * math.erfc(mid / math.sqrt(2)) > alpha:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2


def _uniform_test(counts):
    counts = np.asarray(counts, dtype=float).reshape(-1)
    n = counts.sum()
    k = len(counts)
    if n <= 0:
        return None
    expected = n / k
    stat = float(((counts - expected) ** 2 / expected).sum())
    p = _chi2_sf(stat, k - 1)
    return {"statistic": round(stat, 2), "df": k - 1, "p_value": round(p, 4),
            # effect size: how far the busiest slot sits above an even spread
            "max_excess_pct": round(100.0 * (float(counts.max()) - expected) / expected, 1)}


def _holm(tests):
    """Holm–Bonferroni across the three tests, so running three does not triple the false alarms.

    On the simulated corpus the hour test alone lands at p ~ 0.04 — exactly what one
    of three independent 5% tests produces by chance about one time in seven.
    """
    live = sorted((t["p_value"], name) for name, t in tests.items() if t)
    m = len(live)
    rejecting = True
    for rank, (p, name) in enumerate(live):
        threshold = ALPHA / (m - rank)
        rejecting = rejecting and p < threshold
        tests[name]["pattern"] = bool(rejecting)
        tests[name]["holm_threshold"] = round(threshold, 4)
    return tests


def _label_hour(h):
    return f"{h:02d}:00–{(h + 1) % 24:02d}:00"


def _peak_window(grid):
    """Busiest run of WINDOW_HOURS consecutive hours, wrapping past midnight into the next day."""
    flat = grid.reshape(-1)                         # Monday 00 ... Sunday 23
    ext = np.concatenate([flat, flat[:WINDOW_HOURS - 1]])
    sums = np.convolve(ext, np.ones(WINDOW_HOURS, dtype=float), mode="valid")[: len(flat)]
    i = int(np.argmax(sums))
    d, h = divmod(i, 24)
    end = (h + WINDOW_HOURS) % 24
    return {"weekday": WEEKDAYS[d], "start_hour": h, "end_hour": end,
            "label": f"{WEEKDAYS[d]} {h:02d}:00–{end:02d}:00", "count": int(sums[i]),
            "share": round(float(sums[i]) / max(float(flat.sum()), 1.0), 4)}


def temporal(district=None, crime_type=None):
    f = _frame()
    if f is None:
        return {"error": "No FIR corpus is loaded."}, 503
    options = {"districts": sorted(f["district"].unique().tolist()),
               "crime_types": sorted(c for c in f["crime_type"].unique() if c and c != "Unknown")}
    scope = f
    if district:
        match = next((d for d in options["districts"] if d.lower() == district.strip().lower()), None)
        if match is None:
            return {"error": f"Unknown district '{district}'."}, 400
        district, scope = match, scope[scope["district"] == match]
    if crime_type:
        match = next((c for c in options["crime_types"] if c.lower() == crime_type.strip().lower()), None)
        if match is None:
            return {"error": f"Unknown crime type '{crime_type}'."}, 400
        crime_type, scope = match, scope[scope["crime_type"] == match]

    grid = np.zeros((7, 24), dtype=float)
    if len(scope):
        np.add.at(grid, (scope["weekday"].to_numpy(), scope["hour"].to_numpy()), 1.0)
    n = int(grid.sum())

    # Most common crime type per cell, with its count — only meaningful unfiltered.
    top = [[None] * 24 for _ in range(7)]
    if crime_type is None and n:
        g = scope.groupby(["weekday", "hour", "crime_type"]).size().reset_index(name="n")
        g = g.sort_values(["weekday", "hour", "n", "crime_type"], ascending=[True, True, False, True])
        for r in g.drop_duplicates(["weekday", "hour"]).itertuples(index=False):
            top[r.weekday][r.hour] = {"crime_type": r.crime_type, "count": int(r.n)}

    expected = n / 168.0 if n else 0.0
    z_threshold = _z_crit(ALPHA / 168)
    hot, cold = [], []
    # The normal approximation behind z needs ~5 expected per cell; below that a
    # single unlucky cell would be called "hot".
    if expected >= MIN_FOR_PATTERN / 168:
        z = (grid - expected) / math.sqrt(expected)
        for d in range(7):
            for h in range(24):
                cell = {"weekday": WEEKDAYS[d], "hour": h, "label": f"{WEEKDAYS[d][:3]} {_label_hour(h)}",
                        "count": int(grid[d, h]), "z": round(float(z[d, h]), 2)}
                if z[d, h] >= z_threshold:
                    hot.append(cell)
                elif z[d, h] <= -z_threshold:
                    cold.append(cell)
        hot.sort(key=lambda c: -c["z"])
        cold.sort(key=lambda c: c["z"])

    by_day = grid.sum(axis=1)
    by_hour = grid.sum(axis=0)
    tests = _holm({
        "cells": _uniform_test(grid),
        "hours": _uniform_test(by_hour),
        "weekdays": _uniform_test(by_day),
    }) if n else {}
    peak = None
    if n:
        d, h = np.unravel_index(int(np.argmax(grid)), grid.shape)
        peak = {"weekday": WEEKDAYS[d], "hour": int(h), "label": f"{WEEKDAYS[d]} {_label_hour(int(h))}",
                "count": int(grid[d, h]), "expected": round(expected, 1)}

    body = {
        "filters": {"district": district, "crime_type": crime_type},
        "options": options,
        "firs": n,
        "weekdays": list(WEEKDAYS),
        "hours": list(range(24)),
        "grid": grid.astype(int).tolist(),
        "top_crime": top,
        "by_weekday": [{"weekday": WEEKDAYS[i], "count": int(v)} for i, v in enumerate(by_day)],
        "by_hour": [{"hour": i, "count": int(v)} for i, v in enumerate(by_hour)],
        "max_cell": int(grid.max()) if n else 0,
        "expected_per_cell": round(expected, 2),
        "peak_cell": peak,
        "peak_window": _peak_window(grid) if n else None,
        "busiest_weekday": WEEKDAYS[int(np.argmax(by_day))] if n else None,
        "busiest_hour": _label_hour(int(np.argmax(by_hour))) if n else None,
        "tests": tests,
        "hot_cells": hot[:10],
        "cold_cells": cold[:10],
        "hot_threshold_z": round(z_threshold, 2),
        "note": _note(n, tests, hot),
    }
    return body, 200


def _note(n, tests, hot):
    if not n:
        return "No FIRs in this scope."
    if n < MIN_FOR_PATTERN:
        return (f"{n:,} FIRs across 168 hour-by-day cells is about {n / 168:.1f} a cell — too few for "
                f"any cell to stand out from chance. Read the grid as a record, not a pattern.")
    cells, hours, days = tests["cells"], tests["hours"], tests["weekdays"]
    found = [name for name, t in (("hour of day", hours), ("day of week", days)) if t and t["pattern"]]
    if not cells["pattern"] and not found:
        return ("No time pattern beyond chance: FIRs in scope are spread across hours and weekdays about "
                f"as evenly as random timing would give (hour-by-day p = {cells['p_value']:.2f}, hours "
                f"p = {hours['p_value']:.2f}, weekdays p = {days['p_value']:.2f}; Holm-corrected for "
                f"three tests). The busiest hour runs {hours['max_excess_pct']:.0f}% above an even "
                "spread and the brightest cell is the luck of the draw, not a hotspot. This corpus is "
                "simulated and its incident times are drawn close to uniform.")
    msg = "A time pattern beyond chance: "
    msg += ", ".join(found) if found else "the hour-by-day grid"
    msg += " (Holm-corrected across three tests)."
    if hot:
        msg += f" {len(hot)} cell{'s' if len(hot) != 1 else ''} stand{'s' if len(hot) == 1 else ''} out after correcting for 168 comparisons."
    return msg


@bp.route(f"{API}/analytics/temporal", methods=["GET"])
def route_temporal():
    body, status = temporal(request.args.get("district") or None, request.args.get("crime_type") or None)
    return jsonify(body), status
