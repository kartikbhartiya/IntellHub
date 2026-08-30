"""Recidivism risk — will this offender be named in a new case within a year?

The platform previously reported a "recidivism risk index" that was a hand-weighted
sum of registry flags (30 x prior offences + 20 x arrests + 20 x risk level ...).
That is a scoring rubric, not a model: nothing in it was ever fitted to, or tested
against, an observed outcome, so there was no way to know whether a score of 78
meant anything different from a score of 45.

This module defines an actual supervised target and measures it.

Target
------
Fix an observation date T. Using only records dated before T, describe each
offender. Then look at the window (T, T + 365 days] and label 1 if they are named
in a *new* case during it. The label is therefore always in the future relative
to every feature.

Two disjoint panels
-------------------
Training uses T_train, testing uses T_test = T_train + 365 days, so the training
outcome window closes exactly where the test window opens. No outcome is ever
shared between the two, and no feature is computed from a period the model is
later scored on.

Leakage controls
----------------
* `past_fir_count` / `past_arrests` from CriminalsRegistry are **excluded**. They
  are current-state snapshot columns with no as-of date, so they may well count
  offences committed after T — which is the label. Equivalent counts are
  recomputed from the dated case and arrest tables instead.
* The snapshot boolean flags (rowdy sheeter, history sheet, active warrant) have
  the same problem. The headline model omits them; an ablation that includes them
  is reported alongside, and the gap between the two is exactly the size of the
  optimism those columns would have introduced.
* The old hand-weighted index is scored as a baseline, so "does the learned model
  actually beat the rubric we had?" is answered with a number.
"""
import numpy as np
import pandas as pd

from .. import config, evaluate, monitoring, baselines

NAME = "recidivism_risk"

VIOLENT = {"Murder", "Homicide", "Assault"}
PROPERTY = {"Vehicle Theft", "Chain Snatching", "House Break-in (Night)"}

BASE_FEATURES = [
    "n_prior_cases", "days_since_last", "days_since_first", "career_length_days",
    "offence_rate_per_year", "n_districts", "n_crime_types", "mean_severity",
    "max_severity", "n_co_offenders", "violent_share", "property_share",
    "n_prior_arrests", "age", "recent_cases_180d",
]
SNAPSHOT_FEATURES = ["rowdy_sheeter", "history_sheet", "active_warrant", "has_gang"]

# The rubric the platform used before, kept as a baseline to beat.
_LEGACY_WEIGHTS = {"prior_offences": 30, "prior_arrests": 20, "risk_level": 20,
                   "network_reach": 12, "active_warrant": 10, "gang_ties": 8}
_RISK_WEIGHT = {"Critical": 1.0, "High": 0.75, "Medium": 0.45, "Low": 0.2}
_NO_GANG = ("none", "nan", "independent", "unaffiliated", "na", "")


def _offender_events(data):
    """Dated (offender, case) history — the only source of temporal features."""
    accused = data["accused"]
    cases = data["cases"]
    if accused is None or accused.empty or cases is None or cases.empty:
        return pd.DataFrame()
    ev = accused.merge(
        cases[["case_key", "ts", "district", "crime_type", "severity_score"]],
        on="case_key", how="inner")
    return ev.dropna(subset=["ts"])


def _build_panel(events, arrests, offenders, cutoff, follow_up_days):
    """Feature snapshot as of `cutoff`, label from the window after it."""
    past = events[events["ts"] < cutoff]
    if past.empty:
        return pd.DataFrame()

    horizon_end = cutoff + pd.Timedelta(days=follow_up_days)
    future = events[(events["ts"] >= cutoff) & (events["ts"] < horizon_end)]
    future_offenders = set(future["offender_id"].unique())

    # Co-offenders: distinct people who shared any prior case.
    case_members = past.groupby("case_key")["offender_id"].apply(set)
    arrests_past = (arrests[arrests["arrest_date"] < cutoff]
                    if arrests is not None and not arrests.empty else pd.DataFrame())
    arrest_counts = (arrests_past.groupby("offender_id").size().to_dict()
                     if not arrests_past.empty else {})

    rows = []
    for oid, g in past.groupby("offender_id"):
        first, last = g["ts"].min(), g["ts"].max()
        history_days = (cutoff - first).days
        if history_days < config.RECIDIVISM_MIN_HISTORY_DAYS:
            continue  # too little record to describe them fairly
        career = max(1, (last - first).days)
        co = set()
        for ck in g["case_key"].unique():
            co |= case_members.get(ck, set())
        co.discard(oid)

        rows.append({
            "offender_id": oid,
            "n_prior_cases": len(g),
            "days_since_last": (cutoff - last).days,
            "days_since_first": history_days,
            "career_length_days": career,
            "offence_rate_per_year": len(g) / max(1.0, career / 365.25),
            "n_districts": g["district"].nunique(),
            "n_crime_types": g["crime_type"].nunique(),
            "mean_severity": float(g["severity_score"].mean()),
            "max_severity": float(g["severity_score"].max()),
            "n_co_offenders": len(co),
            "violent_share": float(g["crime_type"].isin(VIOLENT).mean()),
            "property_share": float(g["crime_type"].isin(PROPERTY).mean()),
            "n_prior_arrests": int(arrest_counts.get(oid, 0)),
            "age": float(g["age"].dropna().median()) if g["age"].notna().any() else np.nan,
            "recent_cases_180d": int((g["ts"] >= cutoff - pd.Timedelta(days=180)).sum()),
            "reoffended": int(oid in future_offenders),
        })

    panel = pd.DataFrame(rows)
    if panel.empty:
        return panel
    panel["age"] = panel["age"].fillna(panel["age"].median())

    # Snapshot registry columns, joined for the ablation only.
    if offenders is not None and not offenders.empty:
        snap = offenders.set_index("offender_id")
        for col in ("rowdy_sheeter", "history_sheet", "active_warrant"):
            panel[col] = panel["offender_id"].map(
                snap[col] if col in snap.columns else {}).fillna(False).astype(int)
        gang = panel["offender_id"].map(
            snap["gang_affiliation"] if "gang_affiliation" in snap.columns else {})
        panel["has_gang"] = (~gang.fillna("None").astype(str).str.strip()
                             .str.lower().isin(_NO_GANG)).astype(int)
        panel["risk_level"] = panel["offender_id"].map(
            snap["risk_level"] if "risk_level" in snap.columns else {}).fillna("Low")
        panel["known_associates"] = panel["offender_id"].map(
            snap["known_associates"] if "known_associates" in snap.columns else {}).fillna("")
    else:
        for col in SNAPSHOT_FEATURES:
            panel[col] = 0
        panel["risk_level"] = "Low"
        panel["known_associates"] = ""
    return panel


def legacy_index(panel, dated_only=False):
    """The previous hand-weighted 0-100 rubric, recomputed so it can be scored.

    `dated_only` rebuilds the rubric using only information that carries a date.
    Four of its six terms do not: risk_level, active_warrant and gang ties are
    undated snapshot columns, and `known_associates` is a registry string listing
    every co-offender ever recorded — including ones from cases inside the very
    window being predicted. The dated variant substitutes the panel's
    `n_co_offenders`, which counts only associates from cases before the cutoff.

    Scoring both separates the rubric's genuine skill from the part that comes
    only from being allowed to read current-state fields.
    """
    prior = (panel["n_prior_cases"].clip(0, 10) / 10)
    arr = (panel["n_prior_arrests"].clip(0, 8) / 8)
    w = _LEGACY_WEIGHTS

    if dated_only:
        net = (panel["n_co_offenders"].clip(0, 6) / 6)
        total = w["prior_offences"] + w["prior_arrests"] + w["network_reach"]
        return ((prior * w["prior_offences"] + arr * w["prior_arrests"]
                 + net * w["network_reach"]) * (100.0 / total))

    assoc = panel["known_associates"].fillna("").apply(
        lambda x: len([t for t in str(x).split(";") if t]))
    net = (assoc.clip(0, 6) / 6)
    risk = panel["risk_level"].map(_RISK_WEIGHT).fillna(0.3)
    return (prior * w["prior_offences"] + arr * w["prior_arrests"]
            + net * w["network_reach"] + risk * w["risk_level"]
            + panel["active_warrant"] * w["active_warrant"]
            + panel["has_gang"] * w["gang_ties"])


def train(data, report=None):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import cross_val_score, StratifiedKFold
    from sklearn.metrics import roc_auc_score

    events = _offender_events(data)
    if events.empty:
        return None
    arrests = data.get("arrests")
    offenders = data.get("offenders")

    follow_up = config.RECIDIVISM_FOLLOW_UP_DAYS
    last_ts = events["ts"].max()
    t_test = (last_ts - pd.Timedelta(days=follow_up)).normalize()
    t_train = (t_test - pd.Timedelta(days=follow_up)).normalize()

    train_panel = _build_panel(events, arrests, offenders, t_train, follow_up)
    test_panel = _build_panel(events, arrests, offenders, t_test, follow_up)
    if len(train_panel) < 100 or len(test_panel) < 50:
        return None
    if train_panel["reoffended"].nunique() < 2 or test_panel["reoffended"].nunique() < 2:
        return None

    ytr = train_panel["reoffended"].to_numpy()
    yte = test_panel["reoffended"].to_numpy()
    Xtr = train_panel[BASE_FEATURES]
    Xte = test_panel[BASE_FEATURES]

    def _logreg():
        return Pipeline([("impute", SimpleImputer(strategy="median")),
                         ("scale", StandardScaler()),
                         ("clf", LogisticRegression(max_iter=2000, class_weight="balanced",
                                                    random_state=config.RANDOM_STATE))])

    def _gbm():
        return HistGradientBoostingClassifier(
            max_depth=3, max_iter=250, learning_rate=0.06,
            l2_regularization=1.0, random_state=config.RANDOM_STATE)

    # Model choice is made by cross-validation on the training panel only.
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=config.RANDOM_STATE)
    candidates = {"Logistic Regression": _logreg(), "Gradient Boosting": _gbm()}
    cv_scores = {}
    for label, est in candidates.items():
        try:
            cv_scores[label] = float(np.mean(
                cross_val_score(est, Xtr, ytr, cv=cv, scoring="roc_auc")))
        except Exception:
            cv_scores[label] = float("nan")
    best_label = max(cv_scores, key=lambda k: (cv_scores[k] if np.isfinite(cv_scores[k]) else -1))

    # Probabilities are calibrated: a risk score that drives surveillance decisions
    # has to mean what it says, not merely rank correctly. Platt scaling is used
    # rather than isotonic because isotonic pins its extreme bins to exactly 0 and
    # 1 — and a watchlist entry reading "100% will reoffend" is a claim no model on
    # this much data can support, however good its ranking is.
    model = CalibratedClassifierCV(candidates[best_label], method="sigmoid", cv=cv)
    model.fit(Xtr, ytr)
    prob = model.predict_proba(Xte)[:, 1]
    pred = (prob >= 0.5).astype(int)

    metrics = evaluate.classification_metrics(yte, pred, y_prob=prob, labels=[0, 1])

    # --- baselines, including the rubric this model replaces
    bl = [
        baselines.prior_probability_baseline(ytr, yte),
        baselines.majority_class_baseline(ytr, yte),
    ]
    legacy_full = legacy_dated = None
    try:
        legacy_full = round(float(roc_auc_score(
            yte, legacy_index(test_panel).to_numpy(dtype=float))), 4)
        bl.append({
            "strategy": "legacy rubric (as shipped — reads snapshot columns)",
            "roc_auc": legacy_full,
            "note": "the previous 0-100 scoring rubric, including its risk_level, "
                    "active_warrant and gang terms",
        })
        legacy_dated = round(float(roc_auc_score(
            yte, legacy_index(test_panel, dated_only=True).to_numpy(dtype=float))), 4)
        bl.append({
            "strategy": "legacy rubric (restricted to dated information)",
            "roc_auc": legacy_dated,
            "note": "the same rubric with the undated snapshot terms removed — the "
                    "like-for-like comparison against the model",
        })
    except Exception:
        pass

    # --- ablation: what the snapshot registry flags would have bought us
    ablation = None
    try:
        abl_feats = BASE_FEATURES + SNAPSHOT_FEATURES
        abl = CalibratedClassifierCV(_gbm(), method="sigmoid", cv=cv)
        abl.fit(train_panel[abl_feats], ytr)
        abl_prob = abl.predict_proba(test_panel[abl_feats])[:, 1]
        ablation = {
            "with_snapshot_flags_roc_auc": round(float(roc_auc_score(yte, abl_prob)), 4),
            "headline_roc_auc": metrics.get("roc_auc"),
            "interpretation": (
                "Snapshot registry flags carry no as-of date, so any lift they add "
                "cannot be distinguished from reading the outcome. The headline model "
                "excludes them."),
        }
    except Exception:
        pass

    # --- permutation importance, computed on the test panel
    importances = []
    try:
        from sklearn.inspection import permutation_importance
        pi = permutation_importance(model, Xte, yte, n_repeats=10,
                                    random_state=config.RANDOM_STATE, scoring="roc_auc")
        order = np.argsort(pi.importances_mean)[::-1]
        importances = [{"feature": BASE_FEATURES[i],
                        "importance": round(float(pi.importances_mean[i]), 4),
                        "std": round(float(pi.importances_std[i]), 4)}
                       for i in order]
    except Exception:
        pass

    notes = [
        f"Target: named in a new case within {follow_up} days after the observation date.",
        f"Training panel observed at {t_train.date()}, test panel at {t_test.date()} — "
        "the two outcome windows do not overlap.",
        "Registry columns past_fir_count / past_arrests are excluded: they are undated "
        "snapshots that may already count the offences being predicted. Prior counts are "
        "recomputed from the dated case and arrest tables.",
        f"Model selected by cross-validated ROC-AUC on the training panel "
        f"({best_label}), then calibrated with Platt scaling.",
    ]
    auc = metrics.get("roc_auc")
    if auc is not None and legacy_dated is not None:
        verb = "improves on" if auc > legacy_dated else "does not improve on"
        notes.append(f"Against the like-for-like comparison — the legacy rubric restricted "
                     f"to the same dated information — the model {verb} it: "
                     f"ROC-AUC {auc:.3f} vs {legacy_dated:.3f}.")
    if auc is not None and legacy_full is not None and legacy_full > auc:
        notes.append(
            f"The rubric as previously shipped scores higher ({legacy_full:.3f}), but only "
            "because it reads risk_level, active_warrant and gang affiliation — undated "
            "current-state columns that reflect the offender's whole record, including the "
            "very window being predicted. That advantage is leakage, not skill, which is "
            "why those columns are excluded here and why the ablation below matters.")

    artifact = {
        "name": NAME,
        "estimator": model,
        "_profile": monitoring.build_profile(
            train_panel, numeric=BASE_FEATURES, scores=prob),
        "features": BASE_FEATURES,
        "observed_at": str(t_test.date()),
        "follow_up_days": follow_up,
        "train_positive_rate": round(float(ytr.mean()), 4),
    }

    if report is not None:
        report.add(
            NAME,
            algorithm=f"{best_label} with Platt calibration",
            task="binary classification (probability of re-offence)",
            target=f"new case within {follow_up} days of the observation date",
            features=BASE_FEATURES,
            split={
                "strategy": "two disjoint observation panels (no shared outcome window)",
                "train_observed_at": str(t_train.date()),
                "test_observed_at": str(t_test.date()),
                "n_train": int(len(train_panel)),
                "n_test": int(len(test_panel)),
                "train_positive_rate": round(float(ytr.mean()), 4),
                "test_positive_rate": round(float(yte.mean()), 4),
            },
            metrics=metrics,
            baselines=bl,
            notes=notes,
            extra={
                "cv_roc_auc": {k: (round(v, 4) if np.isfinite(v) else None)
                               for k, v in cv_scores.items()},
                "selected_model": best_label,
                "permutation_importance": importances,
                "snapshot_flag_ablation": ablation,
            },
        )
    return artifact


def make_predictor(artifact):
    """offender feature dict -> calibrated probability of re-offence."""
    if not artifact:
        return None
    est = artifact["estimator"]
    feats = artifact["features"]

    def predict(feature_rows):
        df = pd.DataFrame(feature_rows)
        for c in feats:
            if c not in df.columns:
                df[c] = np.nan
        return est.predict_proba(df[feats])[:, 1]

    return predict


def score_current_offenders(data, artifact, top_n=25):
    """Score every offender as of the most recent data, for the live watchlist."""
    if not artifact:
        return {"scores": {}, "top": []}
    events = _offender_events(data)
    if events.empty:
        return {"scores": {}, "top": []}

    cutoff = events["ts"].max() + pd.Timedelta(days=1)
    panel = _build_panel(events, data.get("arrests"), data.get("offenders"),
                         cutoff, config.RECIDIVISM_FOLLOW_UP_DAYS)
    if panel.empty:
        return {"scores": {}, "top": []}

    predict = make_predictor(artifact)
    probs = predict(panel[artifact["features"]].to_dict("records"))
    panel = panel.assign(_p=probs)

    # Drift is measured against the profile captured at training time, and the
    # watchlist scores are the ones actually acted on, so they are what to compare.
    try:
        drift = monitoring.compare(
            artifact.get("_profile"), panel[artifact["features"]], scores=probs)
    except Exception:
        drift = {"available": False, "reason": "drift comparison failed"}

    names = {}
    offenders = data.get("offenders")
    if offenders is not None and not offenders.empty and "name" in offenders.columns:
        names = dict(zip(offenders["offender_id"], offenders["name"]))

    # The 0-100 index is a percentile rank within the current offender population,
    # not the raw probability. In this corpus the base rate of re-offence is high,
    # so absolute-probability bands would mark a third of the register "Extreme" —
    # useless as a watchlist, which is a ranking instrument. The calibrated
    # probability is carried alongside so the absolute meaning is never lost.
    panel["_pct"] = panel["_p"].rank(pct=True) * 100.0

    def _band(pct):
        return ("Extreme" if pct >= 95 else "High" if pct >= 80
                else "Elevated" if pct >= 50 else "Moderate")

    top = panel.sort_values("_p", ascending=False).head(top_n)
    top_list = [{
        "criminal_id": r["offender_id"],
        "name": names.get(r["offender_id"], r["offender_id"]),
        "risk_index": round(float(r["_pct"]), 1),
        "probability": round(float(r["_p"]), 4),
        # Never display a flat 0% or 100%. No model fitted on a few hundred
        # offenders can support a claim of certainty about a person.
        "reoffence_pct": round(min(99.9, max(0.1, float(r["_p"]) * 100)), 1),
        "band": _band(float(r["_pct"])),
        "past_fir_count": int(r["n_prior_cases"]),
        "days_since_last": int(r["days_since_last"]),
        "active_warrant": bool(r.get("active_warrant", 0)),
        "gang_affiliation": None,
        "drivers": _drivers(r),
    } for _, r in top.iterrows()]

    bands = {b: int(sum(1 for p in panel["_pct"] if _band(p) == b))
             for b in ("Extreme", "High", "Elevated", "Moderate")}

    return {
        "drift": drift,
        "scores": {r["offender_id"]: round(float(r["_pct"]), 1) for _, r in panel.iterrows()},
        "probabilities": {r["offender_id"]: round(float(r["_p"]), 4)
                          for _, r in panel.iterrows()},
        "top": top_list,
        "bands": bands,
        "index_meaning": "percentile rank of predicted re-offence probability within "
                         "the currently registered offender population",
        "mean_probability": round(float(panel["_p"].mean()), 4),
        "base_rate_note": f"{panel['_p'].mean():.0%} mean predicted probability across "
                          f"{len(panel)} offenders — read a band as a rank, not a rate",
        "observed_at": str(cutoff.date()),
        "population": int(len(panel)),
    }


def _drivers(row):
    """Plain-language reasons behind a high score, for the officer reading it."""
    out = []
    if row["n_prior_cases"] >= 4:
        out.append(f"{int(row['n_prior_cases'])} prior cases on record")
    if row["days_since_last"] <= 90:
        out.append(f"active {int(row['days_since_last'])} days ago")
    if row["offence_rate_per_year"] >= 2:
        out.append(f"{row['offence_rate_per_year']:.1f} offences/year")
    if row["n_districts"] >= 3:
        out.append(f"operates across {int(row['n_districts'])} districts")
    if row["n_co_offenders"] >= 4:
        out.append(f"{int(row['n_co_offenders'])} known co-offenders")
    if row["violent_share"] >= 0.5:
        out.append("predominantly violent offences")
    return out[:4]
