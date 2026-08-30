"""Baselines — the "could a trivial rule do this?" reference models.

An accuracy figure means nothing on its own. 82% sounds strong until you learn
that always guessing the majority class scores 79%. Every model in this package
is reported next to the baselines here, and the honest headline number is the
*lift over baseline*, not the raw score.
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- classification
def majority_class_baseline(y_train, y_test):
    """Always predict the most common training label."""
    y_train = np.asarray(y_train)
    y_test = np.asarray(y_test)
    if not len(y_train) or not len(y_test):
        return {"strategy": "majority class", "accuracy": None}
    vals, counts = np.unique(y_train, return_counts=True)
    majority = vals[np.argmax(counts)]
    acc = float(np.mean(y_test == majority))
    return {
        "strategy": "majority class",
        "predicts": str(majority),
        "accuracy": round(acc, 4),
        # Published alongside accuracy because on an imbalanced problem this rule
        # wins on accuracy precisely by never predicting the minority class. Its
        # balanced accuracy is 0.5 by construction — one class has perfect recall,
        # the other zero — and a model beating THAT is the comparison that means
        # something. Without this number, a model with a correctly-set threshold
        # looks worse than a rule that has told the reader nothing.
        "balanced_accuracy": 0.5,
        "mcc": 0.0,
        "note": "Accuracy here is the base rate of the majority class, not skill.",
    }


def prior_probability_baseline(y_train, y_test):
    """Predict the training base rate for every row (a binary-probability baseline).

    Scored with Brier score, so a model that merely reproduces the base rate
    cannot look skilful.
    """
    y_train = np.asarray(y_train, dtype=float)
    y_test = np.asarray(y_test, dtype=float)
    if not len(y_train) or not len(y_test):
        return {"strategy": "base rate", "brier": None}
    p = float(np.mean(y_train))
    return {
        "strategy": "base rate",
        "base_rate": round(p, 4),
        "brier": round(float(np.mean((y_test - p) ** 2)), 4),
        "roc_auc": 0.5,  # a constant predictor is definitionally uninformative
    }


def stratified_random_baseline(y_train, y_test, random_state=42):
    """Sample predictions from the training label distribution."""
    y_train = np.asarray(y_train)
    y_test = np.asarray(y_test)
    if not len(y_train) or not len(y_test):
        return {"strategy": "stratified random", "accuracy": None}
    rng = np.random.default_rng(random_state)
    vals, counts = np.unique(y_train, return_counts=True)
    probs = counts / counts.sum()
    draws = rng.choice(vals, size=len(y_test), p=probs)
    return {
        "strategy": "stratified random",
        "accuracy": round(float(np.mean(draws == y_test)), 4),
    }


# ---------------------------------------------------------------- forecasting
def naive_forecast(history, horizon):
    """Last observed value, repeated. The hardest baseline to beat on noisy counts."""
    h = np.asarray(history, dtype=float)
    if not len(h):
        return np.zeros(horizon)
    return np.repeat(h[-1], horizon)


def seasonal_naive_forecast(history, horizon, period=52):
    """Value from one season ago — the standard reference for seasonal series."""
    h = np.asarray(history, dtype=float)
    if len(h) < period:
        return naive_forecast(h, horizon)
    out = [h[-period + (i % period)] for i in range(horizon)]
    return np.asarray(out, dtype=float)


def moving_average_forecast(history, horizon, window=4):
    """Mean of the last `window` observations, repeated."""
    h = np.asarray(history, dtype=float)
    if not len(h):
        return np.zeros(horizon)
    w = min(window, len(h))
    return np.repeat(float(np.mean(h[-w:])), horizon)


def drift_forecast(history, horizon):
    """Linear extrapolation of the first-to-last trend (Hyndman's drift method)."""
    h = np.asarray(history, dtype=float)
    n = len(h)
    if n < 2:
        return naive_forecast(h, horizon)
    slope = (h[-1] - h[0]) / (n - 1)
    return np.asarray([h[-1] + slope * (i + 1) for i in range(horizon)], dtype=float)


FORECAST_BASELINES = {
    "naive (last value)": naive_forecast,
    "seasonal naive (52w)": seasonal_naive_forecast,
    "moving average (4w)": moving_average_forecast,
    "drift": drift_forecast,
}
