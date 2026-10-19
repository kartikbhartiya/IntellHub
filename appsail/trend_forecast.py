"""Annual crime-trend forecast: one number per district per year, and how far to trust it.

* ``GET /serverless/api/forecast/annual/meta`` — the units, years and crime types the
  forecaster can answer for, plus its model card (backtest against baselines).
* ``GET /serverless/api/forecast/annual?unit=&year=&crime_type=`` — one forecast:
  point, 80% interval, the unit's annual history, per-lakh rate, the trend the model
  used and where it came from, and (for the all-crimes scope) a per-head breakdown.
  A year that is already in the corpus is answered as a *backtest*: the model is
  refitted on the years before it only, and the actual count is shown next to what
  it would have said.
* ``GET /serverless/api/forecast/annual/outlook?year=&crime_type=`` — every district's
  forecast for one year, ranked by expected change, each flagged for whether that
  change is larger than the model's own error band.

The weekly forecaster (``ml/tasks/forecast.py``) answers "how many next week". This
answers the planning question — "how many next year, in this district" — on the same
FIR corpus, aggregated to (district, year).

The model
---------
Ridge regression on ``log(1 + annual count)``, one penalty per block of features:

* log population (2011 Census), unpenalised — Bengaluru Urban and Kodagu differ by
  an order of magnitude and the model must not shrink that away;
* a deviation per district from what its population predicts, penalised toward
  zero — a small district's level is pooled toward the statewide rate instead of
  resting on its own forty-odd FIRs a year;
* one statewide year slope, penalised toward zero;
* a year slope per district, penalised toward zero — a district only earns its own
  trend when its record supports one over the shared one.

That is partial pooling: with six or seven points per district, a free per-district
line chases noise, and a single statewide line ignores real differences. The three
penalties are chosen inside each training window — forward-chaining CV where the
window allows two splits, leave-one-year-out where it does not — so no backtest score
is used to pick them, and a large penalty is the model concluding there is no trend.
Predictions are retransformed with Duan's smearing estimate, because ``exp`` of a mean
log is a biased estimate of the mean.

What the numbers on the card mean
---------------------------------
Every headline metric is out of sample: a rolling-origin backtest refits the model at
each year using only the years before it and scores the year after. Three baselines
are backtested identically — last year's count, the district's mean, and a straight
per-district line — and the model's MAE is compared with the best of them by a paired
bootstrap, which says whether the gap is distinguishable from noise.

R² across district-years is reported because it is the number people expect, but it
is mostly the spread *between* districts (a ~50x range) — any model that knows which
district it is looking at scores near 1. The harder number is R² on the
year-over-year change, which is shown next to it.

The interval is built from backtest residuals on a Poisson-like scale
(``(actual - forecast) / sqrt(forecast)``), so a small district gets a proportionally
wider band than a large one. Its realised coverage is measured leave-one-year-out —
quantiles from the other backtest years, scored on the held-out one — rather than
claimed from the same residuals it was fitted to.

Honest about the corpus
-----------------------
The FIR corpus is simulated (CCTNS-shaped, not KSP records). Its district volumes are
drawn population-weighted and move little from year to year, so there is not much
trend to find; the backtest is what says how much of the movement the model can call,
and the response carries that note. The statewide band adds district variances as if
district errors were independent; the model card shows how many statewide backtest
years actually fell inside it.

Like ``insights`` and ``network_intel``, the data cache is only read through the getter
given to ``configure``: main.py rebinds every table on a reload.
"""
import math
import threading

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, request

import karnataka

bp = Blueprint("trend_forecast", __name__)
API = "/serverless/api"

_cache_getter = lambda: {}   # noqa: E731 — replaced by configure()


def configure(cache_getter):
    """Bind the live data cache. A callable, never the dict itself."""
    global _cache_getter
    _cache_getter = cache_getter


# --------------------------------------------------------------------- constants
STATE = "Karnataka"
MODEL_NAME = "Partial-pooling log-linear ridge regression"
MIN_TRAIN_YEARS = 3          # fewer than this and a year slope is not estimable
MAX_HORIZON = 2              # forecasts beyond two years out are not offered
INTERVAL_LEVEL = 0.80
_Z80 = 1.2815515655446004    # standard normal 90th percentile
BOOTSTRAP_DRAWS = 2000

BASELINES = ("last_year", "district_mean", "district_line")
BASELINE_LABELS = {
    "last_year": "Same as last year",
    "district_mean": "District's average year",
    "district_line": "Straight line per district",
}


# ------------------------------------------------------------------ preparation
_lock = threading.Lock()
_memo = {"key": None, "base": None, "fits": {}, "backtests": {}}


def _complete_years(ts):
    """Calendar years the corpus covers end to end, and the ones it only touches.

    A year counts when the data starts by 15 Jan and runs to at least 15 Dec — a
    part-year would read as a collapse in crime and drag every trend down.
    """
    ts = ts.dropna()
    if ts.empty:
        return [], []
    lo, hi = ts.min(), ts.max()
    full, partial = [], []
    for y in range(int(lo.year), int(hi.year) + 1):
        if lo <= pd.Timestamp(y, 1, 15) and hi >= pd.Timestamp(y, 12, 15):
            full.append(y)
        else:
            partial.append(y)
    return full, partial


def _base():
    """(unit, year, crime_type) rows for the loaded corpus, memoised on its identity.

    A reload or a newly registered FIR rebinds or grows ``firs``; either changes the
    key and drops every fitted model with it.
    """
    cache = _cache_getter() or {}
    firs = cache.get("firs")
    if firs is None or len(firs) == 0:
        return None
    key = (id(firs), len(firs))
    with _lock:
        if _memo["key"] == key:
            return _memo["base"]
    ts = pd.to_datetime(firs["timestamp"], errors="coerce")
    years, partial = _complete_years(ts)
    frame = pd.DataFrame({
        "unit": firs["district"].astype(str),
        "year": ts.dt.year,
        "crime_type": firs["crime_type"].astype(str),
    }).dropna(subset=["year"])
    frame["year"] = frame["year"].astype(int)
    frame = frame[frame["year"].isin(years)]
    units = sorted(u for u in frame["unit"].unique() if u and u.lower() != "nan")
    crime_types = sorted(c for c in frame["crime_type"].unique() if c and c.lower() not in ("nan", "unknown"))
    base = {
        "frame": frame,
        "years": years,
        "partial_years": partial,
        "units": units,
        "crime_types": crime_types,
        "span": (ts.min(), ts.max()),
        "tables": {},
    }
    with _lock:
        _memo.update(key=key, base=base, fits={}, backtests={})
    return base


def _table(base, crime_type):
    """Counts as a units x years matrix (zeros where a district had none)."""
    tkey = crime_type or ""
    with _lock:
        hit = base["tables"].get(tkey)
    if hit is not None:
        return hit
    f = base["frame"]
    if crime_type:
        f = f[f["crime_type"] == crime_type]
    t = (f.groupby(["unit", "year"]).size().unstack(fill_value=0)
         .reindex(index=base["units"], columns=base["years"], fill_value=0)
         .astype(float))
    with _lock:
        base["tables"][tkey] = t
    return t


# ------------------------------------------------------------------ the model
# Penalty grids. Levels are pulled toward what the district's population predicts
# (lam_level); the statewide slope (lam_trend) and each district's own slope
# (lam_own) are pulled toward zero. A large value is the model saying "no evidence".
LAM_LEVEL = (0.3, 3.0, 30.0, 300.0, 3000.0)
LAM_TREND = (0.1, 10.0, 1000.0)
LAM_OWN = (1.0, 30.0, 1000.0)
DEFAULT_LAMBDAS = (3.0, 1000.0, 1000.0)


def _design(unit_idx, t, log_pop, n_units):
    """[log population | district deviation x n | statewide slope | district slope x n]."""
    n = len(t)
    rows = np.arange(n)
    X = np.zeros((n, 2 * n_units + 2))
    X[:, 0] = log_pop[unit_idx]
    X[rows, 1 + unit_idx] = 1.0
    X[:, 1 + n_units] = t
    X[rows, 2 + n_units + unit_idx] = t
    return X


def _penalty(n_units, lams):
    lam_level, lam_trend, lam_own = lams
    return np.concatenate([[1e-6], np.full(n_units, lam_level), [lam_trend], np.full(n_units, lam_own)])


def _solve(X, y, pen, w):
    """Weighted ridge with a penalty per column and an unpenalised intercept."""
    sw = float(w.sum())
    xm, ym = (w @ X) / sw, float(w @ y) / sw
    Xc = X - xm
    Xw = Xc * w[:, None]
    beta = np.linalg.solve(Xw.T @ Xc + np.diag(pen), Xw.T @ (y - ym))
    return beta, ym - float(xm @ beta)


def _weights(unit_idx, counts, n_units):
    """Row weight = the district's mean annual count, normalised to mean 1.

    The variance of log(1 + count) falls roughly as 1/count, so a district logging
    400 FIRs a year has a far more trustworthy record than one logging 25. Weighting
    by the district's *mean* (not each year's own count) keeps a low year from being
    down-weighted for being low, which would bias every level upward.
    """
    means = np.bincount(unit_idx, weights=counts, minlength=n_units) / np.maximum(
        np.bincount(unit_idx, minlength=n_units), 1)
    w = np.maximum(means, 0.5)[unit_idx]
    return w / w.mean()


class _Fit:
    """A fitted model for one scope and one training window."""

    __slots__ = ("lams", "coef", "intercept", "smear", "ref", "train_years", "n_units", "rows",
                 "log_pop", "cv")

    def _log_mean(self, target_year):
        n = self.n_units
        X = _design(np.arange(n), np.full(n, float(target_year - self.ref)), self.log_pop, n)
        return X @ self.coef + self.intercept

    def predict(self, target_year):
        return np.clip(np.exp(self._log_mean(target_year)) * self.smear - 1.0, 0.0, None)

    def trend(self, i):
        """(statewide, district-specific, combined) slope as % per year."""
        n = self.n_units
        pooled = float(self.coef[1 + n])
        own = float(self.coef[2 + n + i])
        pct = lambda b: round(100.0 * math.expm1(b), 2)   # noqa: E731
        return pct(pooled), round(pct(pooled + own) - pct(pooled), 2), pct(pooled + own)

    def population_level(self, i):
        """What population alone predicts for district i, and the district's own pull."""
        n = self.n_units
        base = self.intercept + float(self.coef[0]) * float(self.log_pop[i])
        dev = float(self.coef[1 + i])
        to_count = lambda v: max(0.0, math.exp(v) * self.smear - 1.0)   # noqa: E731
        return to_count(base), to_count(base + dev)


def _rows(table, years):
    """Stacked training rows: unit index, year, raw count."""
    n_units = table.shape[0]
    sub = table[years].to_numpy()                  # units x years
    unit_idx = np.repeat(np.arange(n_units), len(years))
    yrs = np.tile(np.asarray(years, dtype=float), n_units)
    return unit_idx, yrs, sub.reshape(-1)


def _fit_window(unit_idx, yrs, counts, log_pop, n_units, lams, ref):
    X = _design(unit_idx, yrs - ref, log_pop, n_units)
    y = np.log1p(counts)
    beta, b0 = _solve(X, y, _penalty(n_units, lams), _weights(unit_idx, counts, n_units))
    smear = float(np.mean(np.exp(y - (X @ beta + b0))))
    return beta, b0, smear


def _inner_folds(years):
    """Validation splits inside a training window, forecasting-shaped where possible.

    Forward-chaining (fit on the years before k, score year k) when the window holds
    two or more such splits; otherwise leave-one-year-out, which is all a three-year
    window allows. Either way only the training window is touched.
    """
    forward = [([y for y in years if y < k], k) for k in years
               if len([y for y in years if y < k]) >= MIN_TRAIN_YEARS]
    if len(forward) >= 2:
        return forward, "forward-chaining"
    return [([y for y in years if y != k], k) for k in years], "leave-one-year-out"


def _choose_lambdas(table, years, log_pop):
    if len(years) < MIN_TRAIN_YEARS:
        return DEFAULT_LAMBDAS, "default (window too short)"
    n_units = table.shape[0]
    unit_idx, yrs, counts = _rows(table, years)
    folds, scheme = _inner_folds(years)
    best, best_err = DEFAULT_LAMBDAS, float("inf")
    for lams in ((a, b, c) for a in LAM_LEVEL for b in LAM_TREND for c in LAM_OWN):
        err = 0.0
        for train, held in folds:
            tr = np.isin(yrs, train)
            te = yrs == held
            ref = float(np.mean(train))
            beta, b0, smear = _fit_window(unit_idx[tr], yrs[tr], counts[tr], log_pop, n_units, lams, ref)
            X = _design(unit_idx[te], yrs[te] - ref, log_pop, n_units)
            pred = np.clip(np.exp(X @ beta + b0) * smear - 1.0, 0.0, None)
            err += float(np.abs(pred - counts[te]).sum())
        if err < best_err - 1e-9:
            best, best_err = lams, err
    return best, scheme


def _log_pop(units):
    return np.log(np.asarray([karnataka.population(u) for u in units], dtype=float))


def _fit(table, years, lams=None):
    n_units = table.shape[0]
    log_pop = _log_pop(table.index)
    cv = "fixed"
    if lams is None:
        lams, cv = _choose_lambdas(table, years, log_pop)
    unit_idx, yrs, counts = _rows(table, years)
    ref = float(np.mean(years))
    beta, b0, smear = _fit_window(unit_idx, yrs, counts, log_pop, n_units, lams, ref)
    f = _Fit()
    f.lams, f.coef, f.intercept, f.smear = tuple(float(v) for v in lams), beta, b0, smear
    f.ref, f.train_years, f.n_units, f.rows = ref, list(years), n_units, int(len(counts))
    f.log_pop, f.cv = log_pop, cv
    return f


def _fit_cached(base, crime_type, last_train_year):
    key = (crime_type or "", last_train_year)
    with _lock:
        hit = _memo["fits"].get(key)
    if hit is not None:
        return hit
    table = _table(base, crime_type)
    years = [y for y in base["years"] if y <= last_train_year]
    f = _fit(table, years)
    with _lock:
        _memo["fits"][key] = f
    return f


def _baseline(name, history, h):
    """history: units x train-years matrix; returns a forecast per unit, h years out."""
    if name == "last_year":
        return history[:, -1].copy()
    if name == "district_mean":
        return history.mean(axis=1)
    # district_line: OLS line per district through its training years
    n = history.shape[1]
    x = np.arange(n, dtype=float)
    xm = x.mean()
    slope = ((x - xm) * (history - history.mean(axis=1, keepdims=True))).sum(axis=1) / ((x - xm) ** 2).sum()
    return np.clip(history.mean(axis=1) + slope * (n - 1 + h - xm), 0.0, None)


# ------------------------------------------------------------------ backtest
def _r2(actual, pred):
    actual, pred = np.asarray(actual, float), np.asarray(pred, float)
    ss_tot = float(((actual - actual.mean()) ** 2).sum())
    if ss_tot <= 1e-12:
        return None
    return round(1.0 - float(((actual - pred) ** 2).sum()) / ss_tot, 3)


def _scores(actual, pred, last):
    actual, pred, last = (np.asarray(a, float) for a in (actual, pred, last))
    err = np.abs(actual - pred)
    total = float(actual.sum())
    return {
        "mae": round(float(err.mean()), 2),
        "wape_pct": round(100.0 * float(err.sum()) / total, 2) if total > 0 else None,
        "r2_levels": _r2(actual, pred),
        "r2_yoy_change": _r2(actual - last, pred - last),
    }


def _bootstrap(actual, model, other, draws=BOOTSTRAP_DRAWS, seed=0):
    """Paired bootstrap of MAE(other) - MAE(model). Positive means the model is better."""
    d = np.abs(np.asarray(actual) - np.asarray(other)) - np.abs(np.asarray(actual) - np.asarray(model))
    if len(d) < 8:
        return None
    rng = np.random.default_rng(seed)
    boots = d[rng.integers(0, len(d), size=(draws, len(d)))].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    verdict = "better" if lo > 0 else "worse" if hi < 0 else "indistinguishable"
    return {"mae_gap": round(float(d.mean()), 2), "ci95": [round(float(lo), 2), round(float(hi), 2)],
            "verdict": verdict}


def _backtest(base, crime_type):
    """Rolling-origin backtest for one scope, memoised. Out-of-sample by construction."""
    bkey = crime_type or ""
    with _lock:
        hit = _memo["backtests"].get(bkey)
    if hit is not None:
        return hit
    table = _table(base, crime_type)
    years = base["years"]
    mat = table.to_numpy()
    out = {"by_horizon": {}, "state_folds": []}
    for h in range(1, MAX_HORIZON + 1):
        actual, model, last, fold_of = [], [], [], []
        bl = {b: [] for b in BASELINES}
        for j, target in enumerate(years):
            train = [y for y in years if y <= target - h]
            if len(train) < MIN_TRAIN_YEARS or target - h not in years:
                continue
            f = _fit_cached(base, crime_type, train[-1])
            pred = f.predict(target)
            hist = mat[:, [years.index(y) for y in train]]
            truth = mat[:, j]
            actual.append(truth)
            model.append(pred)
            last.append(hist[:, -1])
            fold_of.append(np.full(len(truth), target))
            for b in BASELINES:
                bl[b].append(_baseline(b, hist, h))
            if h == 1:
                out["state_folds"].append({"year": int(target), "predicted": float(pred.sum()),
                                           "actual": float(truth.sum()),
                                           "_var_units": pred})
        if not actual:
            continue
        a, m, l, fo = (np.concatenate(x) for x in (actual, model, last, fold_of))
        res = {
            "folds": int(len(actual)),
            "points": int(len(a)),
            "target_years": sorted({int(y) for y in fo}),
            "model": _scores(a, m, l),
            "baselines": [],
        }
        for b in BASELINES:
            p = np.concatenate(bl[b])
            res["baselines"].append(dict(strategy=b, label=BASELINE_LABELS[b], **_scores(a, p, l)))
        best = min(res["baselines"], key=lambda r: r["mae"])
        res["best_baseline"] = best["strategy"]
        res["skill_vs_last_year"] = _skill(res["model"]["mae"], res["baselines"][0]["mae"])
        res["skill_vs_best_baseline"] = _skill(res["model"]["mae"], best["mae"])
        res["significance_vs_best_baseline"] = _bootstrap(a, m, np.concatenate(bl[best["strategy"]]))
        z = (a - m) / np.sqrt(np.maximum(m, 1.0))
        res["_z"] = z
        res["_fold_of"] = fo
        res["interval"] = _interval_quality(z, fo)
        out["by_horizon"][h] = res
    _state_band_check(out)
    with _lock:
        _memo["backtests"][bkey] = out
    return out


def _skill(model_mae, other_mae):
    if not other_mae:
        return None
    return round(1.0 - model_mae / other_mae, 3)


def _quantiles(z):
    lo, hi = np.percentile(z, [100 * (1 - INTERVAL_LEVEL) / 2, 100 * (1 + INTERVAL_LEVEL) / 2])
    return float(lo), float(hi)


def _interval_quality(z, fold_of):
    """Quantiles for serving, and coverage measured leave-one-year-out."""
    lo, hi = _quantiles(z)
    hits, n = 0, 0
    for y in np.unique(fold_of):
        held = fold_of == y
        if held.all() or (~held).sum() < 10:
            continue
        qlo, qhi = _quantiles(z[~held])
        hits += int(((z[held] >= qlo) & (z[held] <= qhi)).sum())
        n += int(held.sum())
    return {
        "level": INTERVAL_LEVEL,
        "z_low": round(lo, 3),
        "z_high": round(hi, 3),
        "sigma_z": round(float(np.std(z)), 3),
        "realised_coverage": round(hits / n, 3) if n else None,
        "coverage_points": n,
        "method": "leave-one-backtest-year-out",
    }


def _state_band_check(out):
    h1 = out["by_horizon"].get(1)
    folds = out["state_folds"]
    if not h1 or not folds:
        for f in folds:
            f.pop("_var_units", None)
        return
    sigma = h1["interval"]["sigma_z"]
    inside = 0
    for f in folds:
        units = f.pop("_var_units")
        half = _Z80 * sigma * math.sqrt(float(np.maximum(units, 1.0).sum()))
        f["low"], f["high"] = round(max(0.0, f["predicted"] - half)), round(f["predicted"] + half)
        f["predicted"], f["actual"] = round(f["predicted"]), round(f["actual"])
        f["inside"] = bool(f["low"] <= f["actual"] <= f["high"])
        inside += f["inside"]
    out["state_band"] = {"inside": inside, "of": len(folds),
                         "assumption": "district errors independent"}


# ------------------------------------------------------------------ answers
class ForecastError(ValueError):
    pass


def _resolve_unit(base, unit):
    if not unit or unit.strip().lower() in ("karnataka", "state", "statewide", "all"):
        return STATE
    u = unit.strip().lower()
    for name in base["units"]:
        if name.lower() == u:
            return name
    raise ForecastError(f"Unknown unit '{unit}'. Use 'Karnataka' or one of the {len(base['units'])} districts.")


def _resolve_crime(base, crime_type):
    if not crime_type or crime_type.strip().lower() in ("all", "any", ""):
        return None
    c = crime_type.strip().lower()
    for name in base["crime_types"]:
        if name.lower() == c:
            return name
    raise ForecastError(f"Unknown crime type '{crime_type}'.")


def _year_bounds(base):
    years = base["years"]
    return years[0] + MIN_TRAIN_YEARS, years[-1] + MAX_HORIZON


def _resolve_year(base, year):
    lo, hi = _year_bounds(base)
    if year in (None, ""):
        return base["years"][-1] + 1
    try:
        y = int(year)
    except (TypeError, ValueError):
        raise ForecastError(f"Year must be a number between {lo} and {hi}.")
    if y < lo or y > hi:
        raise ForecastError(f"Year {y} is outside what can be forecast honestly: {lo}–{hi}. "
                            f"Earlier years have fewer than {MIN_TRAIN_YEARS} years of history before "
                            f"them; later ones are more than {MAX_HORIZON} years past the data.")
    return y


def _band(pred, h_res, state_units=None):
    """80% band for one district (quantile form) or the state (independent sum)."""
    if state_units is not None:
        half = _Z80 * h_res["interval"]["sigma_z"] * math.sqrt(float(np.maximum(state_units, 1.0).sum()))
        return max(0.0, pred - half), pred + half
    s = math.sqrt(max(pred, 1.0))
    return max(0.0, pred + h_res["interval"]["z_low"] * s), pred + h_res["interval"]["z_high"] * s


def _fmt_years(ys):
    return f"{ys[0]}–{ys[-1]}" if len(ys) > 1 else str(ys[0])


def forecast(unit=None, year=None, crime_type=None, breakdown=True):
    base = _base()
    if base is None or not base["years"]:
        raise ForecastError("No FIR corpus is loaded.")
    unit = _resolve_unit(base, unit)
    crime_type = _resolve_crime(base, crime_type)
    year = _resolve_year(base, year)
    years = base["years"]
    last_data = years[-1]
    kind = "backtest" if year <= last_data else "forecast"
    last_train = year - 1 if kind == "backtest" else last_data
    h = year - last_train
    table = _table(base, crime_type)
    fit = _fit_cached(base, crime_type, last_train)
    preds = fit.predict(year)
    bt = _backtest(base, crime_type)
    h_res = bt["by_horizon"].get(h) or bt["by_horizon"].get(1)

    if unit == STATE:
        hist_series = table.sum(axis=0)
        pred = float(preds.sum())
        low, high = _band(pred, h_res, state_units=preds) if h_res else (None, None)
        pooled = fit.trend(0)[0]
        trend = {"statewide_pct_per_year": pooled, "unit_specific_pct_per_year": None,
                 "combined_pct_per_year": pooled}
        population = round(sum(karnataka.population(d) for d in base["units"]), 2)
        level = None
    else:
        i = base["units"].index(unit)
        hist_series = table.iloc[i]
        pred = float(preds[i])
        low, high = _band(pred, h_res) if h_res else (None, None)
        pooled, own, combined = fit.trend(i)
        trend = {"statewide_pct_per_year": pooled, "unit_specific_pct_per_year": round(own, 2),
                 "combined_pct_per_year": combined}
        population = karnataka.population(unit)
        level = fit.population_level(i)

    history = [{"year": int(y), "count": int(hist_series[y]), "in_training": bool(y <= last_train)}
               for y in years]
    train_hist = [r["count"] for r in history if r["in_training"]]
    last_count = train_hist[-1]
    actual = int(hist_series[year]) if kind == "backtest" else None
    change = round(100.0 * (pred - last_count) / last_count, 1) if last_count else None

    out = {
        "unit": unit,
        "year": int(year),
        "crime_type": crime_type,
        "kind": kind,
        "horizon": int(h),
        "predicted": int(round(pred)),
        "interval": {"low": int(round(low)), "high": int(round(high)), "level": INTERVAL_LEVEL}
        if low is not None else None,
        "last_year": int(last_train),
        "last_count": int(last_count),
        "change_vs_last_pct": change,
        "change_exceeds_band": bool(low is not None and not (low <= last_count <= high)),
        "history": history,
        "actual": actual,
        "error": (actual - int(round(pred))) if actual is not None else None,
        "inside_interval": bool(low <= actual <= high) if (actual is not None and low is not None) else None,
        "per_lakh": round(pred / population, 2) if population else None,
        "population_lakhs_2011": population,
        "trend": trend,
        "trained_on": {"years": fit.train_years, "rows": fit.rows, "districts": len(base["units"]),
                       "penalties": dict(zip(("level", "statewide_trend", "district_trend"), fit.lams)),
                       "penalty_selection": fit.cv},
        "explanation": _explain(unit, fit, train_hist, trend, kind, year, pred, actual, level),
    }
    if breakdown and crime_type is None:
        out["by_crime_type"] = _breakdown(base, unit, year, last_train)
    out["note"] = _corpus_note(base, table)
    return out


def _signed(v):
    """'+1.2' / '-0.4' / '+0.0' — never '-0.0' for a slope that rounds to nothing."""
    r = round(float(v), 1)
    return f"{(0.0 if r == 0 else r):+.1f}"


def _explain(unit, fit, train_hist, trend, kind, year, pred, actual, level=None):
    ty = fit.train_years
    avg = sum(train_hist) / len(train_hist)
    who = "Karnataka" if unit == STATE else unit
    parts = [f"Trained on {_fmt_years(ty)} only. {who} averaged {avg:,.0f} a year over that window."]
    if level:
        pop_only, own = level
        parts.append(f"Population alone (2011 Census) implies about {pop_only:,.0f} a year; "
                     f"{who}'s own record moves that to {own:,.0f}.")
    if abs(trend["combined_pct_per_year"]) < 0.05:
        parts.append("No year-on-year trend survived cross-validation, so the forecast is the "
                     "level, not an extrapolated line.")
    elif unit == STATE:
        parts.append(f"The shared statewide trend is {_signed(trend['statewide_pct_per_year'])}% a year.")
    else:
        parts.append(f"Shared statewide trend {_signed(trend['statewide_pct_per_year'])}%/yr; {who}'s own "
                     f"adjustment, shrunk toward the state, {_signed(trend['unit_specific_pct_per_year'])}%/yr.")
    if kind == "backtest" and actual is not None:
        miss = actual - round(pred)
        parts.append(f"The model would have said {pred:,.0f} for {year}; the corpus recorded {actual:,} "
                     f"({'+' if miss >= 0 else ''}{miss:,.0f}).")
    return " ".join(parts)


def _breakdown(base, unit, year, last_train):
    rows = []
    for c in base["crime_types"]:
        table = _table(base, c)
        fit = _fit_cached(base, c, last_train)
        p = fit.predict(year)
        if unit == STATE:
            pred, last = float(p.sum()), float(table[last_train].sum())
        else:
            i = base["units"].index(unit)
            pred, last = float(p[i]), float(table.iloc[i][last_train])
        rows.append({"crime_type": c, "predicted": int(round(pred)), "last_count": int(last),
                     "change_vs_last_pct": round(100.0 * (pred - last) / last, 1) if last else None})
    rows.sort(key=lambda r: -r["predicted"])
    return rows


def _corpus_note(base, table):
    totals = table.sum(axis=0)
    lo, hi = int(totals.min()), int(totals.max())
    mean = float(totals.mean()) or 1.0
    note = (f"Trained on this platform's FIR corpus, which is simulated (CCTNS-shaped, not KSP "
            f"records). Its statewide volume in scope ranged {lo:,}–{hi:,} a year over "
            f"{_fmt_years(base['years'])} (±{100.0 * (hi - lo) / 2 / mean:.0f}% around the mean), "
            f"so there is little real trend to find. The backtest shows how much of the "
            f"year-to-year movement the model calls.")
    if base["partial_years"]:
        note += (" Part-years left out of training: "
                 + ", ".join(str(y) for y in base["partial_years"]) + ".")
    return note


def outlook(year=None, crime_type=None):
    base = _base()
    if base is None or not base["years"]:
        raise ForecastError("No FIR corpus is loaded.")
    crime_type = _resolve_crime(base, crime_type)
    year = _resolve_year(base, year)
    last_data = base["years"][-1]
    kind = "backtest" if year <= last_data else "forecast"
    last_train = year - 1 if kind == "backtest" else last_data
    h = year - last_train
    table = _table(base, crime_type)
    preds = _fit_cached(base, crime_type, last_train).predict(year)
    h_res = _backtest(base, crime_type)["by_horizon"].get(h)
    rows = []
    for i, u in enumerate(base["units"]):
        pred = float(preds[i])
        last = float(table.iloc[i][last_train])
        low, high = _band(pred, h_res) if h_res else (None, None)
        row = {
            "unit": u,
            "predicted": int(round(pred)),
            "last_count": int(last),
            "change_vs_last_pct": round(100.0 * (pred - last) / last, 1) if last else None,
            "interval": {"low": int(round(low)), "high": int(round(high))} if low is not None else None,
            "change_exceeds_band": bool(low is not None and not (low <= last <= high)),
            "per_lakh": round(pred / karnataka.population(u), 2),
        }
        if kind == "backtest":
            row["actual"] = int(table.iloc[i][year])
        rows.append(row)
    rows.sort(key=lambda r: -(r["change_vs_last_pct"] if r["change_vs_last_pct"] is not None else -1e9))
    return {
        "year": int(year),
        "crime_type": crime_type,
        "kind": kind,
        "horizon": int(h),
        "last_year": int(last_train),
        "districts": rows,
        "state": {"predicted": int(round(float(preds.sum()))), "last_count": int(table[last_train].sum())},
        "note": _corpus_note(base, table),
    }


def _finding(fit):
    """What the cross-validated penalties say about this corpus, in a sentence."""
    lam_level, lam_trend, lam_own = fit.lams
    parts = []
    if lam_level >= max(LAM_LEVEL):
        parts.append("District volumes track 2011 population almost exactly, so cross-validation "
                     "pools each district's level nearly all the way to its population-implied rate.")
    elif lam_level <= min(LAM_LEVEL):
        parts.append("Districts differ from what their population predicts, and cross-validation "
                     "keeps each district's own level.")
    else:
        parts.append("Each district's level is a blend of its own record and what its population predicts.")
    if lam_trend >= max(LAM_TREND) and lam_own >= max(LAM_OWN):
        parts.append("No statewide or district trend survives cross-validation: the forecast is a "
                     "level, not an extrapolated line.")
    elif lam_own >= max(LAM_OWN):
        parts.append("A statewide trend is used; no district earns a trend of its own.")
    else:
        parts.append("Districts carry their own trends, shrunk toward the statewide one.")
    return " ".join(parts)


def _public_backtest(bt):
    out = {}
    for h, res in bt["by_horizon"].items():
        out[str(h)] = {k: v for k, v in res.items() if not k.startswith("_")}
    return out


def meta(crime_type=None):
    base = _base()
    if base is None or not base["years"]:
        raise ForecastError("No FIR corpus is loaded.")
    crime_type = _resolve_crime(base, crime_type)
    lo, hi = _year_bounds(base)
    bt = _backtest(base, crime_type)
    table = _table(base, crime_type)
    final = _fit_cached(base, crime_type, base["years"][-1])
    return {
        "units": [STATE] + base["units"],
        "crime_types": base["crime_types"],
        "years": {"min": lo, "max": hi, "data_years": base["years"],
                  "backtest_years": [y for y in range(lo, base["years"][-1] + 1)],
                  "forecast_years": [y for y in range(base["years"][-1] + 1, hi + 1)],
                  "partial_years": base["partial_years"], "default": base["years"][-1] + 1},
        "model": {
            "name": MODEL_NAME,
            "target": "log(1 + FIRs per district per year)",
            "features": ["log population (2011 Census)", "district deviation from population (penalised)",
                         "statewide year slope (penalised)", "district year slope (penalised)"],
            "rows": int(table.size),
            "districts": len(base["units"]),
            "penalty_grid": {"level": list(LAM_LEVEL), "statewide_trend": list(LAM_TREND),
                             "district_trend": list(LAM_OWN)},
            "penalty_selection": "forward-chaining CV inside each training window "
                                 "(leave-one-year-out when the window is too short)",
            "retransformation": "Duan smearing",
            "interval": "backtest residuals on sqrt(forecast) scale; 80%",
            "backtest": _public_backtest(bt),
            "statewide_backtest": bt.get("state_folds", []),
            "statewide_band_check": bt.get("state_band"),
            "selected_penalties": dict(zip(("level", "statewide_trend", "district_trend"), final.lams)),
            "finding": _finding(final),
            "r2_caveat": ("R² across district-years is dominated by the spread between districts; "
                          "R² on the year-over-year change is the harder test."),
        },
        "crime_type": crime_type,
        "note": _corpus_note(base, table),
    }


# ------------------------------------------------------------------ routes
def _answer(fn, **kw):
    try:
        return jsonify(fn(**kw))
    except ForecastError as e:
        return jsonify({"error": str(e)}), 400


@bp.route(f"{API}/forecast/annual/meta", methods=["GET"])
def route_meta():
    return _answer(meta, crime_type=request.args.get("crime_type"))


@bp.route(f"{API}/forecast/annual", methods=["GET"])
def route_forecast():
    return _answer(forecast, unit=request.args.get("unit"), year=request.args.get("year"),
                   crime_type=request.args.get("crime_type"))


@bp.route(f"{API}/forecast/annual/outlook", methods=["GET"])
def route_outlook():
    return _answer(outlook, year=request.args.get("year"), crime_type=request.args.get("crime_type"))
