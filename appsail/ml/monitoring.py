"""Runtime model monitoring: what the models are actually doing once deployed.

The offline evaluation in this package is strong — temporal splits, baselines,
calibration, leakage probes. All of it describes the models on the day they were
trained. Nothing described them afterwards.

That gap matters more here than the offline rigour does. A model trained on a
corpus where 42% of prosecutions ended in conviction, running two years later
against a district where filing practice has changed, will keep returning
confident probabilities and nothing in the system would notice. The registry
could not even tell you what it had predicted last week.

Three things live here:

**Prediction logging.** Every score the platform hands to a person is recorded —
model, subject, value, and the time. This is the raw material for everything
else: without a record of what was predicted, "was it right?" is unanswerable
and no feedback loop can ever be built.

**Reference profiles.** At training time each model stores the distribution of
its own input features and output scores. That is the baseline drift is measured
against, and it has to be captured then — reconstructing it later from a corpus
that has already moved is circular.

**Drift detection** via Population Stability Index, the standard measure in
credit risk for exactly this question. PSI compares a current distribution
against the reference:

    < 0.10   stable
    0.10-0.25  moderate shift, worth watching
    > 0.25   significant shift, the model is being asked about a different
             population than the one it learned

PSI is reported per feature and for the score distribution. It detects that the
inputs moved; it cannot tell you the model got worse, because that needs
outcomes, and outcomes need the feedback loop this file's prediction log is the
first half of. Stated plainly rather than implied.
"""
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np

from . import config

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREDICTION_LOG = os.environ.get("PREDICTION_LOG_PATH") or os.path.join(
    _DIR, "predictions.jsonl")

_LOCK = threading.Lock()
_RECENT = deque(maxlen=5000)
_MAX_BYTES = 16 * 1024 * 1024

# PSI bands, from the credit-risk convention.
PSI_STABLE = 0.10
PSI_SIGNIFICANT = 0.25

# Quantile edges used to bucket a numeric feature for PSI. Ten buckets is the
# usual choice: enough resolution to see a shift, few enough that a small sample
# does not produce empty bins and an infinite ratio.
_N_BUCKETS = 10


# ---------------------------------------------------------------- prediction log
def log_prediction(model, subject_id, score, subject_type=None, extra=None):
    """Record one score handed to a person. Never raises."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "subject_type": subject_type,
        "subject_id": str(subject_id) if subject_id is not None else None,
        "score": round(float(score), 6) if score is not None else None,
    }
    if extra:
        entry.update(extra)

    line = json.dumps(entry, separators=(",", ":"), default=str)
    try:
        print("PREDICTION " + line, file=sys.stdout, flush=True)
    except Exception:
        pass
    with _LOCK:
        _RECENT.append(entry)
        try:
            _rotate_if_needed()
            with open(PREDICTION_LOG, "a", encoding="utf-8") as fh:
                fh.write(line + chr(10))
        except Exception as e:
            print("PREDICTION-SINK-FAILURE " + str(e), file=sys.stderr, flush=True)
    return entry


def _rotate_if_needed():
    """Caller holds _LOCK."""
    try:
        if os.path.getsize(PREDICTION_LOG) < _MAX_BYTES:
            return
    except OSError:
        return
    rolled = PREDICTION_LOG + ".1"
    try:
        if os.path.exists(rolled):
            os.remove(rolled)
        os.replace(PREDICTION_LOG, rolled)
    except OSError as e:
        print("PREDICTION-ROTATE-FAILURE " + str(e), file=sys.stderr, flush=True)


def recent_predictions(model=None, limit=500):
    """A window over the in-memory deque. Convenience for dashboards only.

    NOT a complete record: the deque is capped and starts empty on every restart.
    Anything that must not miss a score — a disclosure report above all — has to read
    the log with `predictions_for`.
    """
    rows = [r for r in _RECENT if model is None or r.get("model") == model]
    return rows[::-1][:limit]


def predictions_for(subject_id, model=None):
    """Every score on record for one subject, read from disk.

    Reads the rolled generation first so the order stays chronological across a
    rotation. This is the function a disclosure or audit path must use; the deque
    behind `recent_predictions` is a cache and forgets everything on restart.
    """
    subject = str(subject_id)
    rows = []
    for path in (PREDICTION_LOG + ".1", PREDICTION_LOG):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue        # a torn line loses one score, not the rest
                    if str(entry.get("subject_id")) != subject:
                        continue
                    if model and entry.get("model") != model:
                        continue
                    rows.append(entry)
        except OSError:
            continue
    return rows


# ---------------------------------------------------------------- profiles
def build_profile(frame, numeric=None, categorical=None, scores=None):
    """Capture the distribution a model was trained on.

    Numeric features are stored as bucket edges plus the share of mass in each
    bucket; categoricals as category shares. Edges come from the *reference*
    data and are reused later, which is the point — comparing two independently
    bucketed distributions measures the bucketing, not the drift.
    """
    profile = {"n": int(len(frame)) if frame is not None else 0,
               "numeric": {}, "categorical": {}}

    for col in (numeric or []):
        if frame is None or col not in frame:
            continue
        values = frame[col].astype(float).dropna().to_numpy()
        if values.size < _N_BUCKETS:
            continue
        edges = np.unique(np.quantile(values, np.linspace(0, 1, _N_BUCKETS + 1)))
        if edges.size < 3:
            continue          # degenerate: a near-constant column tells us nothing
        counts, _ = np.histogram(values, bins=edges)
        profile["numeric"][col] = {
            "edges": [float(e) for e in edges],
            "shares": _shares(counts),
            "mean": float(values.mean()),
        }

    for col in (categorical or []):
        if frame is None or col not in frame:
            continue
        counts = frame[col].astype(str).value_counts()
        total = float(counts.sum()) or 1.0
        profile["categorical"][col] = {
            str(k): round(float(v) / total, 6) for k, v in counts.head(50).items()
        }

    if scores is not None and len(scores):
        arr = np.asarray(scores, dtype=float)
        edges = np.unique(np.quantile(arr, np.linspace(0, 1, _N_BUCKETS + 1)))
        if edges.size >= 3:
            counts, _ = np.histogram(arr, bins=edges)
            profile["score"] = {
                "edges": [float(e) for e in edges],
                "shares": _shares(counts),
                "mean": float(arr.mean()),
            }
    return profile


def _shares(counts):
    total = float(np.sum(counts)) or 1.0
    return [round(float(c) / total, 6) for c in counts]


# ---------------------------------------------------------------- drift
def psi(reference_shares, current_shares):
    """Population Stability Index between two bucketed distributions.

    An empty bucket on either side makes the log term infinite, so both are
    floored at a small epsilon. That is the conventional treatment and it bounds
    the result; without it one empty bucket dominates the whole statistic.
    """
    eps = 1e-6
    ref = np.clip(np.asarray(reference_shares, dtype=float), eps, None)
    cur = np.clip(np.asarray(current_shares, dtype=float), eps, None)
    n = min(len(ref), len(cur))
    if n == 0:
        return None
    ref, cur = ref[:n], cur[:n]
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def band(value):
    if value is None:
        return "unknown"
    if value < PSI_STABLE:
        return "stable"
    return "moderate" if value < PSI_SIGNIFICANT else "significant"


def compare(profile, frame, scores=None):
    """Measure the current data against a stored reference profile."""
    if not profile:
        return {"available": False,
                "reason": "no reference profile was captured at training time"}

    features, worst = [], 0.0
    for col, ref in (profile.get("numeric") or {}).items():
        if frame is None or col not in frame:
            continue
        values = frame[col].astype(float).dropna().to_numpy()
        if not values.size:
            continue
        counts, _ = np.histogram(values, bins=np.asarray(ref["edges"], dtype=float))
        value = psi(ref["shares"], _shares(counts))
        if value is None:
            continue
        worst = max(worst, value)
        features.append({"feature": col, "kind": "numeric",
                         "psi": round(value, 4), "band": band(value),
                         "reference_mean": round(ref.get("mean", 0.0), 4),
                         "current_mean": round(float(values.mean()), 4)})

    for col, ref in (profile.get("categorical") or {}).items():
        if frame is None or col not in frame:
            continue
        counts = frame[col].astype(str).value_counts()
        total = float(counts.sum()) or 1.0
        keys = sorted(set(ref) | set(counts.index.astype(str)))
        value = psi([ref.get(k, 0.0) for k in keys],
                    [float(counts.get(k, 0)) / total for k in keys])
        if value is None:
            continue
        worst = max(worst, value)
        unseen = sorted(set(counts.index.astype(str)) - set(ref))[:5]
        features.append({"feature": col, "kind": "categorical",
                         "psi": round(value, 4), "band": band(value),
                         "unseen_categories": unseen})

    report = {
        "available": bool(features),
        "reference_n": profile.get("n"),
        "current_n": int(len(frame)) if frame is not None else 0,
        "features": sorted(features, key=lambda f: -f["psi"]),
        "worst_psi": round(worst, 4),
        "verdict": band(worst),
        "thresholds": {"stable_below": PSI_STABLE, "significant_above": PSI_SIGNIFICANT},
        "interpretation": (
            "PSI compares the current input distribution against the one captured "
            "when the model was trained. It detects that the population moved; it "
            "does NOT show the model got worse — that needs outcomes, which need "
            "the feedback loop the prediction log is the first half of."),
    }

    if scores is not None and profile.get("score") is not None and len(scores):
        ref = profile["score"]
        counts, _ = np.histogram(np.asarray(scores, dtype=float),
                                 bins=np.asarray(ref["edges"], dtype=float))
        value = psi(ref["shares"], _shares(counts))
        report["score_psi"] = round(value, 4) if value is not None else None
        report["score_band"] = band(value)
        report["score_note"] = (
            "A shifted score distribution with stable inputs usually means the "
            "model is being applied to a different slice than it was measured on.")
    return report
