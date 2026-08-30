"""Replay the held-out test window through the serving path and the feedback loop.

Why this exists
---------------
`ml/outcomes.py` can measure whether predictions came true, and reports "no outcomes
recorded yet" because none have been — the platform has never been used against live
cases. That is honest, and it also means the single most important check in an ML
system has never actually run end to end.

The corpus already contains the answers for the held-out window: real recorded
verdicts and chargesheet dates the models were never trained on. Scoring that window
through the *serving* artifact and feeding the known results into the feedback loop
does two things a training run cannot:

1. **It detects training/serving skew.** Offline evaluation scores a model inside the
   training script, with the training script's own frame. Serving loads a joblib and
   scores a frame rebuilt elsewhere. Those two paths can diverge — a dropped column, a
   changed dtype, a different category encoding — and every symptom is silent: the
   model still returns numbers, they are just worse. Comparing the replayed metric
   against the published one is how that gets caught.
2. **It exercises the loop.** Prediction log, join, maturity window, base-rate
   baseline, calibration bands — none of it had ever run on real pairs.

What this is NOT
----------------
It is not evidence the system is in use, and it must never be read as field
performance. Every entry it writes carries `source="replay"`, `realised()` reports the
breakdown by source, and `/models/performance` shows it — so a replayed number can
never be mistaken for an operational one. The outcomes are real records from the
corpus; the *observation dates* are synthetic scaffolding, set to the score date plus
the model's own maturity window, because the loop needs a date and the corpus does not
always carry a resolution timestamp.

Preview by default, like the retention tooling: writing to the prediction and outcome
sinks is a real mutation of the runtime record.

    python replay_outcomes.py                      # preview both models
    python replay_outcomes.py --model conviction_outcome --apply
"""
import argparse
import json
import os
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ml import config, dataset, registry, splits  # noqa: E402
from ml import outcomes as ml_outcomes  # noqa: E402
from ml import monitoring as ml_monitoring  # noqa: E402

# How each model's held-out window is rebuilt. Everything here mirrors the training
# task exactly; a replay against a different split would compare two different things
# and the comparison would mean nothing.
PLANS = {
    "conviction_outcome": {
        "task": "conviction",
        "time_col": "ts",
        "subject_col": "offender_id",
        "outcome": {1: "convicted", 0: "acquitted"},
    },
    "case_closure": {
        "task": "allocation",
        "time_col": "registered_ts",
        "subject_col": "case_id",
        "outcome": {1: "closed", 0: "still_open"},
    },
}


def _split_for(name, frame):
    """Reproduce the task's own split, including its fallback."""
    if name == "conviction_outcome":
        train, test, cutoff = splits.grouped_temporal_split(frame, "offender_id", "ts")
        if len(test) < 40 or train["y"].nunique() < 2 or test["y"].nunique() < 2:
            train, test, cutoff = splits.temporal_split(frame, "ts")
        return test, cutoff
    train, test, cutoff = splits.temporal_split(frame, "registered_ts")
    return test, cutoff


def _published(name):
    path = os.path.join(HERE, "artifacts", "model_registry.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh).get("models") or {}).get(name, {}).get("metrics", {})
    except (OSError, ValueError):
        return {}


def replay(name, apply=False, limit=None, data=None):
    """`data` accepts an already-loaded canonical bundle.

    The running application holds these frames in memory; re-reading 38 CSVs per call
    was most of a 35-second request and pushed the endpoint past the gateway's 30s cap.
    The CLI still loads them itself, because it has nothing to reuse.
    """
    plan = PLANS[name]
    artifact = registry.load_artifact(name)
    if not artifact:
        return {"model": name, "error": "no artifact — run `python -m ml.train` first"}

    task = __import__(f"ml.tasks.{plan['task']}", fromlist=["build_frame"])
    data = data if data is not None else dataset.load_canonical()
    frame = task.build_frame(data)
    if frame is None or frame.empty:
        return {"model": name, "error": "the task produced no frame"}

    test, cutoff = _split_for(name, frame)

    # `limit` bounds how many pairs get WRITTEN, never what gets scored. Scoring is
    # vectorised and costs nothing; the file appends are what take time. Comparing a
    # sampled metric against a metric computed on the full held-out set is comparing
    # two different things, and doing so raised a false "training/serving skew" alarm
    # the first time this ran with a limit — a check that cries wolf is worse than no
    # check at all.
    features = artifact["features"]
    estimator = artifact["estimator"]
    # The serving path: a frame rebuilt outside the training script, scored by the
    # persisted estimator. This is the call whose agreement with offline evaluation
    # is in question.
    prob = estimator.predict_proba(test[features])[:, 1]

    subject = plan["subject_col"]
    time_col = plan["time_col"]
    window = ml_outcomes.MIN_OBSERVATION_DAYS[name]
    labels = plan["outcome"]

    scored_at = pd.to_datetime(test[time_col], errors="coerce")
    written = 0
    if apply:
        # A seeded random sample, not the first N rows. `head()` takes the
        # chronologically earliest cases in the held-out window, so the pairs written
        # to the loop were a biased slice — and the realised metrics computed from
        # them inherited that bias. Live, a 150-row head produced a skill score of
        # -0.031 and the loop correctly called the model no better than its base
        # rate, while the full held-out set gives +0.077. The loop was right about
        # what it was given; what it was given was wrong.
        if limit and limit < len(test):
            to_write = test.sample(n=limit, random_state=config.RANDOM_STATE)
        else:
            to_write = test
        positions = {idx: i for i, idx in enumerate(test.index)}
        for _, row in to_write.iterrows():
            i = positions[row.name]
            ts = scored_at.iloc[i]
            if pd.isna(ts):
                continue
            observed = ts + timedelta(days=window + 1)
            ml_monitoring.log_prediction(
                name, row[subject], float(prob[i]),
                subject_type="offender" if subject == "offender_id" else "case",
                extra={"ts": ts.isoformat(timespec="seconds"), "source": "replay"})
            ml_outcomes.record_outcome(
                name, row[subject], labels[int(row["y"])],
                observed_at=observed.isoformat(timespec="seconds"),
                recorded_by="replay_outcomes.py", source="replay",
                notes=f"held-out window after {cutoff}; observation date is the score "
                      f"date plus the model's {window}-day maturity window")
            written += 1

    # Scored here as well as in the loop, so the two can be compared: this is the
    # metric the serving path produces, before any join or maturity filtering.
    y = test["y"].to_numpy()
    from sklearn.metrics import roc_auc_score, brier_score_loss
    served = {
        "n": int(len(y)),
        "roc_auc": round(float(roc_auc_score(y, prob)), 4) if len(set(y)) > 1 else None,
        "brier": round(float(brier_score_loss(y, prob)), 4),
        "base_rate": round(float(y.mean()), 4),
    }
    pub = _published(name)
    pub_auc = pub.get("roc_auc")
    pub_brier = pub.get("brier")

    skew = None
    if pub_auc is not None and served["roc_auc"] is not None:
        d = served["roc_auc"] - pub_auc
        skew = {
            "roc_auc_published": pub_auc,
            "roc_auc_served": served["roc_auc"],
            "difference": round(d, 4),
            # Identical inputs through an identical estimator should agree to the
            # rounding. Anything larger means the frame the serving path rebuilds is
            # not the frame the model was scored on.
            "verdict": ("serving path reproduces offline evaluation"
                        if abs(d) <= 0.005 else
                        "TRAINING/SERVING SKEW — the served frame differs from the "
                        "one offline evaluation used"),
        }
    return {"model": name, "cutoff": str(cutoff), "written": written,
            "write_limit": limit, "applied": apply, "served": served,
            "published": {"roc_auc": pub_auc, "brier": pub_brier}, "skew": skew}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", choices=sorted(PLANS), action="append")
    ap.add_argument("--apply", action="store_true",
                    help="write to the prediction and outcome logs (default: preview)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    for name in (args.model or sorted(PLANS)):
        r = replay(name, apply=args.apply, limit=args.limit)
        print(f"\n=== {name}")
        if r.get("error"):
            print("   ", r["error"])
            continue
        s = r["served"]
        print(f"    held-out rows      : {s['n']}  (cutoff {r['cutoff']}, "
              f"base rate {s['base_rate']})")
        print(f"    served  ROC-AUC    : {s['roc_auc']}   Brier {s['brier']}")
        print(f"    published ROC-AUC  : {r['published']['roc_auc']}   "
              f"Brier {r['published']['brier']}")
        if r["skew"]:
            print(f"    -> {r['skew']['verdict']} (diff {r['skew']['difference']:+})")
        print(f"    written to the loop: {r['written']}"
              + ("" if r["applied"] else "   (preview — pass --apply to write)"))


if __name__ == "__main__":
    main()
