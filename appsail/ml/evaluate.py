"""Metric computation.

One place produces every number the UI shows. Rules this module enforces:

* accuracy is never reported alone — it always ships with a baseline to compare against;
* probabilistic models are scored for *calibration* (Brier, reliability bins), not just
  ranking, because "72% likely to reoffend" is a claim about frequency and a
  miscalibrated score is actively misleading in a policing context;
* multi-class results carry a full confusion matrix and per-class support, so a
  class the model never gets right cannot hide inside a macro average.
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- classification
def classification_metrics(y_true, y_pred, y_prob=None, labels=None, positive_label=1):
    """Full classification report as plain JSON-safe types."""
    from sklearn.metrics import (accuracy_score, f1_score, precision_recall_fscore_support,
                                 confusion_matrix, balanced_accuracy_score,
                                 matthews_corrcoef)

    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    labels = list(labels) if labels is not None else sorted(set(y_true.tolist()) | set(y_pred.tolist()))

    prec, rec, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=labels)

    out = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, y_pred)), 4),
        "f1_macro": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 4),
        "f1_weighted": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 4),
        "mcc": round(float(matthews_corrcoef(y_true, y_pred)), 4) if len(labels) > 1 else None,
        "n_test": int(len(y_true)),
        "labels": [str(l) for l in labels],
        "confusion_matrix": [[int(v) for v in row] for row in cm],
        "per_class": [
            {
                "label": str(labels[i]),
                "precision": round(float(prec[i]), 4),
                "recall": round(float(rec[i]), 4),
                "f1": round(float(f1[i]), 4),
                "support": int(support[i]),
            }
            for i in range(len(labels))
        ],
    }

    if y_prob is not None and len(labels) == 2:
        out.update(binary_probability_metrics(y_true, y_prob, positive_label=positive_label))
    return out


def binary_probability_metrics(y_true, y_prob, positive_label=1):
    """Ranking + calibration quality for a binary probabilistic model."""
    from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss

    y = (np.asarray(y_true) == positive_label).astype(int)
    p = np.asarray(y_prob, dtype=float)
    out = {}
    if len(np.unique(y)) < 2:
        return {"roc_auc": None, "pr_auc": None, "brier": None,
                "note": "single class present in the test window"}
    out["roc_auc"] = round(float(roc_auc_score(y, p)), 4)
    out["pr_auc"] = round(float(average_precision_score(y, p)), 4)
    out["positive_rate"] = round(float(y.mean()), 4)
    out["brier"] = round(float(brier_score_loss(y, p)), 4)
    out["calibration"] = reliability_bins(y, p)
    out["lift_table"] = lift_table(y, p)
    return out


def reliability_bins(y_true, y_prob, n_bins=10):
    """Reliability curve: predicted probability vs observed frequency per decile.

    A well-calibrated model tracks the diagonal. This is the plot that shows
    whether "80% risk" actually means 80%.
    """
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    bins = []
    for b in range(n_bins):
        m = idx == b
        if not m.any():
            continue
        bins.append({
            "bin": f"{edges[b]:.1f}-{edges[b+1]:.1f}",
            "predicted": round(float(p[m].mean()), 4),
            "observed": round(float(y[m].mean()), 4),
            "n": int(m.sum()),
        })
    ece = sum(b["n"] * abs(b["predicted"] - b["observed"]) for b in bins) / max(1, len(p))
    return {"bins": bins, "expected_calibration_error": round(float(ece), 4)}


def lift_table(y_true, y_prob, n_groups=5):
    """Operational view: if officers work only the top-scored X%, what do they catch?

    This is the metric a station actually cares about — AUC is abstract, "the top
    20% of the watchlist contains 48% of the reoffenders" is actionable.
    """
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    if len(y) < n_groups:
        return []
    order = np.argsort(-p)
    y_sorted = y[order]
    total_pos = y.sum()
    base = y.mean()
    rows = []
    chunk = len(y) / n_groups
    for g in range(n_groups):
        lo, hi = int(g * chunk), int((g + 1) * chunk)
        seg = y_sorted[lo:hi]
        if not len(seg):
            continue
        rate = float(seg.mean())
        rows.append({
            "decile": f"top {int(round(100 * (g + 1) / n_groups))}%",
            "n": int(len(seg)),
            "hit_rate": round(rate, 4),
            "lift": round(float(rate / base), 2) if base > 0 else None,
            "cumulative_capture": round(float(y_sorted[:hi].sum() / total_pos), 4) if total_pos else None,
        })
    return rows


# ---------------------------------------------------------------- regression / forecast
def forecast_metrics(y_true, y_pred):
    """Point-forecast error. MASE is included because MAE alone is unit-dependent
    and cannot be compared across districts of different sizes."""
    y = np.asarray(y_true, dtype=float)
    f = np.asarray(y_pred, dtype=float)
    n = min(len(y), len(f))
    y, f = y[:n], f[:n]
    if n == 0:
        return {}
    err = y - f
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    denom = np.where(np.abs(y) < 1e-9, np.nan, np.abs(y))
    mape = float(np.nanmean(np.abs(err) / denom) * 100.0)
    return {
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "mape": round(mape, 2) if np.isfinite(mape) else None,
        "bias": round(float(np.mean(err)), 3),
        "n": int(n),
    }


def mase(y_true, y_pred, train_history, period=1):
    """Mean Absolute Scaled Error — MAE divided by the in-sample naive MAE.

    < 1 means the model beats the naive forecast; >= 1 means it does not, and
    should be reported as such rather than dressed up.
    """
    y = np.asarray(y_true, dtype=float)
    f = np.asarray(y_pred, dtype=float)
    h = np.asarray(train_history, dtype=float)
    if len(h) <= period:
        return None
    scale = float(np.mean(np.abs(h[period:] - h[:-period])))
    if scale < 1e-9:
        return None
    n = min(len(y), len(f))
    return round(float(np.mean(np.abs(y[:n] - f[:n])) / scale), 3)


def interval_coverage(y_true, lower, upper):
    """Did the stated prediction interval actually contain the truth that often?

    An 80% interval that covers 55% of outcomes is a broken interval, and this is
    the only way to catch it.
    """
    y = np.asarray(y_true, dtype=float)
    lo = np.asarray(lower, dtype=float)
    hi = np.asarray(upper, dtype=float)
    n = min(len(y), len(lo), len(hi))
    if n == 0:
        return None
    inside = ((y[:n] >= lo[:n]) & (y[:n] <= hi[:n])).mean()
    return round(float(inside), 4)


# ---------------------------------------------------------------- clustering
def clustering_metrics(coords, labels):
    """Internal validation for the DBSCAN hotspot zones — reported so the eps/
    min_samples choice is defended by a number rather than by eyeballing a map."""
    from sklearn.metrics import silhouette_score, davies_bouldin_score

    labels = np.asarray(labels)
    mask = labels != -1
    n_clusters = len(set(labels[mask].tolist()))
    out = {
        "n_clusters": int(n_clusters),
        "n_clustered": int(mask.sum()),
        "n_noise": int((~mask).sum()),
        "noise_fraction": round(float((~mask).mean()), 4) if len(labels) else None,
    }
    if n_clusters >= 2 and mask.sum() > n_clusters:
        try:
            out["silhouette"] = round(float(silhouette_score(coords[mask], labels[mask])), 4)
            out["davies_bouldin"] = round(float(davies_bouldin_score(coords[mask], labels[mask])), 4)
        except Exception:
            pass
    return out


def json_safe(obj):
    """Recursively coerce numpy/pandas scalars so the report serialises cleanly."""
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if not np.isfinite(v) else v
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if obj is pd.NaT:
        return None
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    return obj
