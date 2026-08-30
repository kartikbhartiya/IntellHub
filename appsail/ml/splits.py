"""Splitters.

Crime records are events on a timeline. A random `train_test_split` lets a model
learn from cases registered *after* the ones it is scored on, which quietly
inflates every metric — the model gets to see the future. Every supervised task
in this package uses one of the splitters below instead.
"""
import numpy as np
import pandas as pd

from . import config


def temporal_split(df, time_col="ts", test_fraction=None):
    """Split on a date boundary: everything before the cutoff trains, after tests.

    Returns (train_df, test_df, cutoff_timestamp). Rows with an unparseable
    timestamp are dropped rather than silently landing in the training half.
    """
    test_fraction = config.TEST_FRACTION if test_fraction is None else test_fraction
    d = df.dropna(subset=[time_col]).sort_values(time_col)
    if len(d) < 20:
        return d, d.iloc[0:0], None
    cutoff = d[time_col].quantile(1.0 - test_fraction)
    train = d[d[time_col] < cutoff]
    test = d[d[time_col] >= cutoff]
    # Degenerate case: heavy timestamp ties can empty one side.
    if len(train) < 10 or len(test) < 10:
        n = int(len(d) * (1.0 - test_fraction))
        train, test = d.iloc[:n], d.iloc[n:]
        cutoff = d[time_col].iloc[n] if n < len(d) else None
    return train, test, cutoff


def grouped_temporal_split(df, group_col, time_col="ts", test_fraction=None):
    """Temporal split that also forbids a group straddling the boundary.

    Used where repeated rows share an entity (e.g. several cases naming the same
    offender). Without this, near-duplicate rows about one person appear on both
    sides and the model is rewarded for memorising the person, not the pattern.
    Groups are assigned to the test side by the timing of their *first* record.
    """
    test_fraction = config.TEST_FRACTION if test_fraction is None else test_fraction
    d = df.dropna(subset=[time_col]).sort_values(time_col)
    if len(d) < 20:
        return d, d.iloc[0:0], None
    first_seen = d.groupby(group_col)[time_col].min().sort_values()
    n_test_groups = max(1, int(len(first_seen) * test_fraction))
    test_groups = set(first_seen.index[-n_test_groups:])
    cutoff = first_seen.iloc[-n_test_groups] if n_test_groups <= len(first_seen) else None
    train = d[~d[group_col].isin(test_groups)]
    test = d[d[group_col].isin(test_groups)]
    return train, test, cutoff


def walk_forward_folds(n_points, n_folds=None, horizon=None, min_train=None):
    """Expanding-window origins for time-series backtesting.

    Yields (train_end_index, test_slice) pairs. Fold k trains on points
    [0, origin) and is scored on [origin, origin + horizon) — i.e. the model is
    re-fitted at each origin using only information available at that moment,
    which is how a forecast would actually have been issued in production.
    """
    n_folds = config.BACKTEST_FOLDS if n_folds is None else n_folds
    horizon = config.FORECAST_HORIZON if horizon is None else horizon
    min_train = config.BACKTEST_MIN_TRAIN_WEEKS if min_train is None else min_train

    last_origin = n_points - horizon
    if last_origin <= min_train:
        return []
    origins = np.linspace(min_train, last_origin, num=n_folds, dtype=int)
    origins = sorted(set(int(o) for o in origins))
    return [(o, slice(o, o + horizon)) for o in origins]


def describe_split(train, test, cutoff, time_col="ts"):
    """Split provenance recorded alongside every metric, so a reviewer can see
    exactly which records produced the number."""
    def _span(d):
        if d is None or len(d) == 0 or time_col not in d:
            return None
        t = pd.to_datetime(d[time_col], errors="coerce").dropna()
        if not len(t):
            return None
        return {"from": str(t.min().date()), "to": str(t.max().date())}

    return {
        "strategy": "temporal (train strictly before cutoff)",
        "cutoff": str(pd.Timestamp(cutoff).date()) if cutoff is not None and pd.notna(cutoff) else None,
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "train_span": _span(train),
        "test_span": _span(test),
    }
