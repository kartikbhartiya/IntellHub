"""Record what actually happened to the people the models scored.

Why this exists
---------------
Everything else in the ML layer measures the models against history. Offline
evaluation says how they did on a held-out slice of the past; drift monitoring says
whether today's inputs still look like that past. Neither can answer the only
question that matters once the system is in use: *are the answers still right?*

Drift is not a proxy for it. Inputs can stay perfectly stable while a model's
accuracy collapses — a change in charging practice, a new court, a shift in what
gets recorded — and PSI will read `stable` throughout. The reverse is just as
common: inputs move because the world moved, and the model is fine.

The only way to answer it is to write down what the prediction was, wait, and then
write down what happened. This module is that second half.

Shape
-----
Two append-only logs, joined on `(model, subject_id)`:

  predictions.jsonl   written by `monitoring.log_prediction` at serving time
  outcomes.jsonl      written here when someone records a real result

`realised(model)` joins them and reports measured performance against the metric the
registry published at training time. A gap between the two is the signal that
offline evaluation has stopped describing reality.

What this does not do
---------------------
It does not make outcomes appear. Until real results are recorded the join is empty
and every function here says so rather than returning a comfortable zero. An empty
feedback loop is still better than none: it is the difference between "we have not
measured this" and "we cannot".
"""
import json
import os
import sys
import threading
from datetime import datetime, timezone

_DIR = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_DIR)

OUTCOME_LOG = os.environ.get("OUTCOME_LOG_PATH") or os.path.join(_APP, "outcomes.jsonl")
_LOCK = threading.Lock()
_MAX_BYTES = 16 * 1024 * 1024

# What a recorded outcome may say, per model. Constrained deliberately: a free-text
# outcome column becomes unusable within a month, and there is no way to compute a
# Brier score against "acquitted (appeal pending)".
LABELS = {
    "conviction_outcome": {"convicted": 1, "acquitted": 0},
    "recidivism_risk": {"reoffended": 1, "no_reoffence": 0},
    "case_closure": {"closed": 1, "still_open": 0},
    "case_pendency": {"breached": 1, "within_horizon": 0},
}

# A prediction is only wrong once enough time has passed for it to have been right.
# Recording "no_reoffence" a week after scoring measures nothing, so the join reports
# how long each pair actually waited and callers can require a minimum.
MIN_OBSERVATION_DAYS = {
    "conviction_outcome": 180,
    "recidivism_risk": 365,
    "case_closure": 90,
    "case_pendency": 90,
}


class UnknownOutcome(ValueError):
    """Raised for an outcome label the model has no definition for."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_outcome(model, subject_id, outcome, observed_at=None,
                   recorded_by=None, source=None, notes=None):
    """Write down what actually happened. Returns the stored entry.

    Unlike the prediction log this *does* raise on a bad label: a prediction that
    fails to log is a lost measurement, but an outcome logged under a label nothing
    can interpret silently poisons every metric computed from it afterwards.
    """
    if model not in LABELS:
        raise UnknownOutcome(
            f"no outcome vocabulary for model {model!r}; known: {sorted(LABELS)}")
    vocabulary = LABELS[model]
    if outcome not in vocabulary:
        raise UnknownOutcome(
            f"{outcome!r} is not a recognised outcome for {model}; "
            f"expected one of {sorted(vocabulary)}")

    entry = {
        "ts": _now(),
        "model": model,
        "subject_id": str(subject_id),
        "outcome": outcome,
        "label": vocabulary[outcome],
        "observed_at": observed_at or _now(),
        "recorded_by": recorded_by,
        "source": source or "manual",
        "notes": notes,
    }

    line = json.dumps(entry, separators=(",", ":"), default=str)
    try:
        print("OUTCOME " + line, file=sys.stdout, flush=True)
    except Exception:
        pass
    with _LOCK:
        try:
            _rotate_if_needed()
            with open(OUTCOME_LOG, "a", encoding="utf-8") as fh:
                fh.write(line + chr(10))
        except Exception as e:
            print("OUTCOME-SINK-FAILURE " + str(e), file=sys.stderr, flush=True)
    return entry


def _rotate_if_needed():
    """Caller holds _LOCK."""
    try:
        if os.path.getsize(OUTCOME_LOG) < _MAX_BYTES:
            return
    except OSError:
        return
    rolled = OUTCOME_LOG + ".1"
    try:
        if os.path.exists(rolled):
            os.remove(rolled)
        os.replace(OUTCOME_LOG, rolled)
    except OSError as e:
        print("OUTCOME-ROTATE-FAILURE " + str(e), file=sys.stderr, flush=True)


def _read(path):
    """Every entry in a sink, oldest first, across the rolled generation.

    Reading only the live file was a real bug rather than an omission. Both sinks roll
    at 16MB, and the observation windows here are 180 to 365 days — so in the ordinary
    case a prediction rolls out of the live file long before its outcome is recorded.
    Missing it did not fail loudly: the pair was silently counted as an outcome with
    no prediction, and realised performance was then measured only over whatever
    happened to be recent. A measurement that quietly narrows to a biased subset is
    worse than one that is absent.
    """
    rows = []
    for candidate in (path + ".1", path):
        try:
            with open(candidate, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue    # a torn line, not a reason to lose the rest
        except OSError:
            continue
    return rows


def _parse(ts):
    """Parse a log timestamp, always returning an aware datetime.

    Writers do not all produce the same shape: `datetime.now(timezone.utc)` is
    offset-aware, while a timestamp carried over from source data is usually naive.
    Comparing the two raises TypeError, and here that took out the whole retention
    endpoint with a 500 — a data-protection control cannot be one unusual log line
    away from being unable to report its own schedule. A naive stamp is read as UTC,
    which is what every writer in this codebase means by one.
    """
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def join(model=None, prediction_log=None):
    """Pair each recorded outcome with the prediction that preceded it.

    The prediction taken is the most recent one *at or before* the observation. A
    subject can be scored many times as their record changes; measuring the outcome
    against a score produced after the fact would be leakage of exactly the kind the
    training pipeline was built to avoid.
    """
    from . import monitoring

    preds = _read(prediction_log or monitoring.PREDICTION_LOG)
    outs = _read(OUTCOME_LOG)
    if model:
        preds = [p for p in preds if p.get("model") == model]
        outs = [o for o in outs if o.get("model") == model]

    by_subject = {}
    for p in preds:
        key = (p.get("model"), str(p.get("subject_id")))
        by_subject.setdefault(key, []).append(p)
    for rows in by_subject.values():
        rows.sort(key=lambda r: str(r.get("ts") or ""))

    pairs, unmatched = [], 0
    for out in outs:
        key = (out.get("model"), str(out.get("subject_id")))
        candidates = by_subject.get(key) or []
        observed = _parse(out.get("observed_at") or out.get("ts"))

        chosen = None
        for p in candidates:
            scored = _parse(p.get("ts"))
            if observed and scored and scored > observed:
                break               # scored after the fact; ignore this and later ones
            chosen = p
        if chosen is None:
            unmatched += 1
            continue

        scored_at = _parse(chosen.get("ts"))
        waited = None
        if scored_at and observed:
            waited = round((observed - scored_at).total_seconds() / 86400.0, 1)

        pairs.append({
            "model": out.get("model"),
            "subject_id": out.get("subject_id"),
            "score": chosen.get("score"),
            "scored_at": chosen.get("ts"),
            "outcome": out.get("outcome"),
            "label": out.get("label"),
            "observed_at": out.get("observed_at"),
            "observation_days": waited,
            "source": out.get("source"),
        })

    return {"pairs": pairs, "outcomes_without_a_prediction": unmatched}


def _brier(pairs):
    return sum((p["score"] - p["label"]) ** 2 for p in pairs) / len(pairs)


def _skill_interval(pairs, base_rate, n_boot=4000, seed=0):
    """Confidence interval on how much the model beats predicting the base rate.

    Paired over the same pairs, because both predictors are scored on identical
    outcomes and the pairing removes the between-subject variation that would
    otherwise swamp a difference this small.
    """
    import random

    if len(pairs) < 20:
        return None
    # Positive means the model beat the base rate on that pair.
    d = [((base_rate - p["label"]) ** 2) - ((p["score"] - p["label"]) ** 2)
         for p in pairs]
    rng = random.Random(seed)
    n = len(d)
    means = []
    for _ in range(n_boot):
        means.append(sum(d[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot) - 1]
    return {"brier_gain_vs_base_rate": round(sum(d) / n, 5),
            "ci95": [round(lo, 5), round(hi, 5)],
            "n_pairs": n}


def _calibration(pairs, bins=5):
    """Predicted probability against observed frequency, in equal-width bands.

    The headline number a calibration claim rests on: when this model says 0.7, does
    it happen 70% of the time? Reported per band because a model can be well
    calibrated on average and badly wrong at the top, which is the only end anyone
    acts on.
    """
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        band = [p for p in pairs
                if (p["score"] >= lo and (p["score"] < hi or (i == bins - 1
                                                              and p["score"] <= hi)))]
        if not band:
            out.append({"band": f"{lo:.1f}-{hi:.1f}", "n": 0,
                        "mean_predicted": None, "observed_rate": None})
            continue
        out.append({
            "band": f"{lo:.1f}-{hi:.1f}",
            "n": len(band),
            "mean_predicted": round(sum(p["score"] for p in band) / len(band), 4),
            "observed_rate": round(sum(p["label"] for p in band) / len(band), 4),
        })
    return out


def realised(model, threshold=0.5, min_days=None, min_pairs=30):
    """Measured performance against what the registry published at training time.

    `min_pairs` is not decoration. Below roughly thirty pairs the difference between
    a realised and a published Brier score is dominated by sampling noise, and
    reporting it invites someone to retrain on a swing that was never there. Under
    that count the numbers are still returned, flagged `sufficient: false`, so the
    coverage is visible without being actionable.
    """
    if model not in LABELS:
        raise UnknownOutcome(f"no outcome vocabulary for model {model!r}")

    min_days = MIN_OBSERVATION_DAYS.get(model, 0) if min_days is None else min_days
    joined = join(model)
    usable = [p for p in joined["pairs"]
              if p["score"] is not None and p["label"] is not None]
    matured = [p for p in usable
               if p["observation_days"] is None or p["observation_days"] >= min_days]

    # Where each outcome came from. A replayed backtest and a result recorded by an
    # officer produce identical numbers and mean completely different things, so the
    # provenance travels with the metric rather than being left to a caption
    # somewhere else.
    sources = {}
    for p in matured:
        sources[p.get("source") or "unknown"] = sources.get(p.get("source") or "unknown", 0) + 1

    report = {
        "model": model,
        "sources": sources,
        "operational_outcomes": sum(n for k, n in sources.items() if k != "replay"),
        "outcomes_recorded": len(usable) + joined["outcomes_without_a_prediction"],
        "matched_to_a_prediction": len(usable),
        "outcomes_without_a_prediction": joined["outcomes_without_a_prediction"],
        "observation_window_days": min_days,
        "matured": len(matured),
        "not_yet_matured": len(usable) - len(matured),
        "sufficient": len(matured) >= min_pairs,
        "minimum_for_a_verdict": min_pairs,
    }

    if not matured:
        report["verdict"] = "no outcomes recorded yet"
        report["note"] = (
            "This is the honest state of a system that has not been used long enough "
            "to know whether its predictions come true. Drift monitoring cannot "
            "substitute: it reports that the inputs moved, never that the answers "
            "got worse.")
        return report

    positives = sum(p["label"] for p in matured)
    correct = sum(1 for p in matured
                  if (p["score"] >= threshold) == bool(p["label"]))

    base_rate = positives / len(matured)
    brier = _brier(matured)
    # Brier score of the only model that needs no data: always predict the base rate.
    # Everything above this line is skill; at or below it, the model is contributing
    # nothing a constant could not.
    baseline = base_rate * (1.0 - base_rate)
    skill = (1.0 - brier / baseline) if baseline > 0 else None

    report.update({
        "observed_positive_rate": round(base_rate, 4),
        "mean_predicted": round(sum(p["score"] for p in matured) / len(matured), 4),
        "accuracy_at_threshold": round(correct / len(matured), 4),
        "threshold": threshold,
        "brier": round(brier, 4),
        "baseline_brier": round(baseline, 4),
        "baseline": "always predict the observed base rate",
        "skill_score": round(skill, 4) if skill is not None else None,
        "skill_interval": _skill_interval(matured, base_rate),
        "calibration": _calibration(matured),
    })

    published = _published_metrics(model)
    if published:
        report["published"] = published
        report["comparison"] = _compare(report, published)

    if sources and not report["operational_outcomes"]:
        report["provenance_warning"] = (
            "Every outcome here came from a replay of the held-out window, not from "
            "operational use. It demonstrates that the serving path reproduces "
            "offline evaluation and that the feedback loop runs end to end. It is "
            "NOT evidence of field performance, and no prediction made in service of "
            "a real decision has yet been checked.")
    return report


def _published_metrics(model):
    """What training-time evaluation claimed, from the registry."""
    path = os.path.join(_APP, "artifacts", "model_registry.json")
    try:
        with open(path, encoding="utf-8") as fh:
            entry = (json.load(fh).get("models") or {}).get(model) or {}
    except (OSError, ValueError):
        return None
    metrics = entry.get("metrics") or {}
    keep = {k: metrics[k] for k in ("accuracy", "brier", "brier_score", "roc_auc")
            if k in metrics}
    if not keep:
        return None
    keep["split"] = (entry.get("split") or {}).get("strategy")
    return keep


def _compare(report, published):
    """State plainly whether reality has drifted from the published claim.

    The thresholds here are conventions, not findings, and are named as such: a Brier
    score 0.05 worse than published is the point at which the gap stops being
    explicable by sampling at these volumes.
    """
    pub_brier = published.get("brier", published.get("brier_score"))
    notes = []
    # `verdict` answers one question only: does this beat predicting the base rate?
    # Until the interval can be computed, the honest answer is that there is not
    # enough to say — not a comparison against something else.
    verdict = "not enough matured outcomes to judge skill"

    # Checked before the published comparison, and allowed to override it. A model
    # can sit within tolerance of a published number that was itself close to
    # no-skill; "matches what we claimed" is not the same as "is useful".
    skill = report.get("skill_score")
    interval = report.get("skill_interval")
    if skill is not None:
        notes.append(f"skill score {skill:+.4f} against a base-rate baseline of "
                     f"{report['baseline_brier']:.4f}")
    # The interval, not the point estimate, decides. A count threshold cannot catch a
    # case where the effect and its noise are the same size — which is exactly what
    # happened here: 905 pairs gave +0.077 and a random 146 of them gave -0.024, and
    # the count rule called the second one a finding.
    if interval:
        lo, hi = interval["ci95"]
        notes.append(f"Brier gain over the base rate "
                     f"{interval['brier_gain_vs_base_rate']:+.5f} "
                     f"[95% CI {lo:+.5f}, {hi:+.5f}] on {interval['n_pairs']} pairs")
        if hi < 0:
            verdict = ("realised performance is worse than predicting the base rate "
                       "— the model is contributing no skill on these outcomes")
        elif lo > 0:
            verdict = "beats the base-rate baseline on the outcomes recorded so far"
        else:
            verdict = ("indistinguishable from predicting the base rate at this "
                       "sample size - the interval spans zero, so these outcomes "
                       "support no conclusion either way")
    elif report["sufficient"] and skill is not None and skill <= 0:
        verdict = ("realised performance is below the base rate, but too few pairs "
                   "to say so with any confidence")

    # A separate answer to a separate question. The interval says whether the model
    # beats the base rate; this says whether reality matches what was published
    # offline. Letting the second overwrite the first buried the finding under a
    # caveat about data collection.
    vs_published = None
    if pub_brier is not None and report.get("brier") is not None:
        delta = report["brier"] - pub_brier
        notes.append(f"Brier {report['brier']:.4f} realised vs {pub_brier:.4f} "
                     f"published ({delta:+.4f})")
        if not report["sufficient"]:
            vs_published = "too few matured outcomes to compare against published"
        elif delta > 0.05:
            vs_published = "materially worse than published evaluation"
        elif delta < -0.05:
            vs_published = ("better than published — check the outcomes are not being "
                            "recorded selectively")
        else:
            vs_published = "consistent with published evaluation"

    pub_acc = published.get("accuracy")
    if pub_acc is not None and report.get("accuracy_at_threshold") is not None:
        notes.append(f"accuracy {report['accuracy_at_threshold']:.4f} realised vs "
                     f"{pub_acc:.4f} published")

    return {"verdict": verdict, "vs_published": vs_published, "detail": notes,
            "caveat": "Recorded outcomes are not a random sample of predictions. "
                      "Cases that reach a result differ systematically from those "
                      "that do not, so this compares against published metrics "
                      "rather than replacing them."}


def coverage():
    """How much of the feedback loop is actually closed, per model."""
    from . import monitoring

    preds = _read(monitoring.PREDICTION_LOG)
    outs = _read(OUTCOME_LOG)
    models = sorted(set([p.get("model") for p in preds] +
                        [o.get("model") for o in outs] + list(LABELS)))
    rows = {}
    for name in models:
        if not name:
            continue
        n_pred = sum(1 for p in preds if p.get("model") == name)
        n_out = sum(1 for o in outs if o.get("model") == name)
        rows[name] = {
            "predictions_logged": n_pred,
            "outcomes_recorded": n_out,
            "outcome_rate": round(n_out / n_pred, 4) if n_pred else None,
            "accepts_outcomes": name in LABELS,
            "labels": sorted(LABELS.get(name, {})),
            "observation_window_days": MIN_OBSERVATION_DAYS.get(name),
        }
    return {
        "models": rows,
        "prediction_log": monitoring.PREDICTION_LOG,
        "outcome_log": OUTCOME_LOG,
        "note": "A model with predictions and no outcomes cannot be known to still "
                "work. Drift monitoring reports that its inputs moved, not that its "
                "answers got worse.",
    }
