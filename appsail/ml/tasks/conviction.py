"""Trial outcome — will a named accused be convicted?

Used to help prosecutors and IOs see which pending trials look evidentially weak
early enough to strengthen them.

This is the model in the platform that most needs guard rails, so it carries the
most:

* **Grouped temporal split.** Rows are split so that no individual appears in
  both halves *and* the test half is later in time. Without the grouping, a
  prolific offender with eight cases seeds both sides and the model is scored
  partly on how well it memorised that person.
* **No snapshot registry flags.** `rowdy_sheeter`, `history_sheet` and
  `active_warrant` are undated current-state columns; a warrant recorded today
  may well be a consequence of the very conviction being predicted. Prior history
  is recomputed from dated case and arrest records instead.
* **Calibration reported.** A number shown next to a person's name as a
  likelihood of conviction has to mean what it says, so Brier score and a
  reliability curve are part of the headline, not an appendix.
* **Fairness slices.** Accuracy is broken out by age band and gender so a model
  that works well overall but poorly for one group cannot pass unnoticed.
"""
import numpy as np
import pandas as pd

from .. import config, evaluate, monitoring, splits, baselines

NAME = "conviction_outcome"

FEATURES = ["age", "gender", "n_prior_cases", "n_prior_arrests", "days_since_first_case",
            "n_co_accused", "severity_score", "fatalities", "injured", "witnesses",
            "n_prior_districts", "arrest_delay_days", "chargesheet_delay_days",
            "crime_type", "district"]
NUMERIC = ["age", "gender", "n_prior_cases", "n_prior_arrests", "days_since_first_case",
           "n_co_accused", "severity_score", "fatalities", "injured", "witnesses",
           "n_prior_districts", "arrest_delay_days", "chargesheet_delay_days"]
CATEGORICAL = ["crime_type", "district"]

# Investigative timeliness. Both are known before a verdict is returned and are
# among the strongest signals a prosecutor actually has: a case where the arrest
# and the chargesheet came quickly is a case where the evidence held together.
# They are *not* leakage — both dates precede the trial they help predict.


def build_frame(data):
    cases, accused, courts = data["cases"], data["accused"], data["courts"]
    if any(x is None or x.empty for x in (cases, accused, courts)):
        return pd.DataFrame()
    if "conviction_status" not in courts.columns:
        return pd.DataFrame()

    verdicts = courts[courts["conviction_status"].isin(["Convicted", "Acquitted"])]
    if verdicts.empty:
        return pd.DataFrame()

    ev = accused.merge(
        cases[["case_key", "ts", "district", "crime_type", "severity_score",
               "fatalities", "injured", "witnesses", "accused_count"]],
        on="case_key", how="inner").dropna(subset=["ts"])
    if ev.empty:
        return pd.DataFrame()

    arrests = data.get("arrests")
    arrests = (arrests.dropna(subset=["arrest_date"])
               if arrests is not None and not arrests.empty else pd.DataFrame())

    # Investigative timeliness per case: days from registration to first arrest and
    # to the chargesheet. Both events precede the trial, so neither leaks the verdict.
    reg_ts = cases.set_index("case_key")["registered_ts"].fillna(
        cases.set_index("case_key")["ts"])
    arrest_delay, cs_delay = {}, {}
    if not arrests.empty:
        first_arrest = arrests.groupby("case_key")["arrest_date"].min()
        arrest_delay = ((first_arrest - reg_ts.reindex(first_arrest.index)).dt.days).to_dict()
    chargesheets = data.get("chargesheets")
    if chargesheets is not None and not chargesheets.empty:
        first_cs = chargesheets.groupby("case_key")["chargesheet_date"].min()
        cs_delay = ((first_cs - reg_ts.reindex(first_cs.index)).dt.days).to_dict()

    # Prior history is counted strictly before each row's own case date.
    ev = ev.sort_values("ts")
    rows = []
    for oid, g in ev.groupby("offender_id"):
        g = g.sort_values("ts")
        a_dates = (arrests[arrests["offender_id"] == oid]["arrest_date"].sort_values().to_numpy()
                   if not arrests.empty else np.array([], dtype="datetime64[ns]"))
        first_ts = g["ts"].iloc[0]
        for i, (_, r) in enumerate(g.iterrows()):
            rows.append({
                "case_key": r["case_key"],
                "offender_id": oid,
                "ts": r["ts"],
                "age": r["age"],
                "gender": r["gender"],
                "n_prior_cases": i,
                "n_prior_arrests": int((a_dates < np.datetime64(r["ts"])).sum()) if len(a_dates) else 0,
                "days_since_first_case": int((r["ts"] - first_ts).days),
                "n_prior_districts": int(g.iloc[:i]["district"].nunique()) if i else 0,
                "n_co_accused": int(max(0, (r["accused_count"] or 1) - 1)),
                "severity_score": r["severity_score"],
                "fatalities": r["fatalities"],
                "injured": r["injured"],
                "witnesses": r["witnesses"],
                "arrest_delay_days": arrest_delay.get(r["case_key"], np.nan),
                "chargesheet_delay_days": cs_delay.get(r["case_key"], np.nan),
                "crime_type": r["crime_type"],
                "district": r["district"],
            })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    outcome = verdicts.set_index("case_key")["conviction_status"]
    frame["y"] = frame["case_key"].map(outcome).map({"Convicted": 1, "Acquitted": 0})
    frame = frame.dropna(subset=["y"])
    frame["y"] = frame["y"].astype(int)
    frame["age"] = pd.to_numeric(frame["age"], errors="coerce")
    frame["gender"] = pd.to_numeric(frame["gender"], errors="coerce").fillna(1)
    return frame


def train(data, report=None):
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from sklearn.impute import SimpleImputer
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import cross_val_score, StratifiedKFold

    frame = build_frame(data)
    if len(frame) < 200 or frame["y"].nunique() < 2:
        return None

    train_df, test_df, cutoff = splits.grouped_temporal_split(frame, "offender_id", "ts")
    if len(test_df) < 40 or train_df["y"].nunique() < 2 or test_df["y"].nunique() < 2:
        # Grouping can starve the test half on a small corpus; fall back to a plain
        # temporal split and say so rather than silently reverting to a random one.
        train_df, test_df, cutoff = splits.temporal_split(frame, "ts")
        grouped = False
        if len(test_df) < 40 or test_df["y"].nunique() < 2:
            return None
    else:
        grouped = True

    Xtr, ytr = train_df[FEATURES], train_df["y"].to_numpy()
    Xte, yte = test_df[FEATURES], test_df["y"].to_numpy()

    # sparse_output=False: with 31 districts the one-hot block tips the
    # ColumnTransformer over its sparse_threshold, and HistGradientBoosting
    # rejects sparse input. Left sparse, the gradient-boosting candidate raises
    # inside cross_val_score, gets scored NaN by the except-branch below and is
    # silently dropped from selection — so the "best model" would be chosen from
    # a field of one without anything saying so.
    pre = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")),
                          ("scale", StandardScaler())]), NUMERIC),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=10,
                              sparse_output=False), CATEGORICAL),
    ])
    candidates = {
        "Logistic Regression": Pipeline([
            ("pre", pre),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced",
                                       random_state=config.RANDOM_STATE))]),
        "Gradient Boosting": Pipeline([
            ("pre", pre),
            ("clf", HistGradientBoostingClassifier(
                max_depth=3, max_iter=200, learning_rate=0.06,
                l2_regularization=1.0, random_state=config.RANDOM_STATE))]),
    }
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=config.RANDOM_STATE)
    cv_scores = {}
    for label, est in candidates.items():
        try:
            cv_scores[label] = float(np.mean(cross_val_score(est, Xtr, ytr, cv=cv, scoring="roc_auc")))
        except Exception:
            cv_scores[label] = float("nan")
    best_label = max(cv_scores, key=lambda k: (cv_scores[k] if np.isfinite(cv_scores[k]) else -1))

    model = CalibratedClassifierCV(candidates[best_label], method="sigmoid", cv=cv)
    model.fit(Xtr, ytr)
    prob = model.predict_proba(Xte)[:, 1]
    pred = (prob >= 0.5).astype(int)
    metrics = evaluate.classification_metrics(yte, pred, y_prob=prob, labels=[0, 1])

    bl = [baselines.prior_probability_baseline(ytr, yte),
          baselines.majority_class_baseline(ytr, yte)]

    fairness = _fairness_slices(test_df, yte, prob, pred)

    importances = []
    try:
        from sklearn.inspection import permutation_importance
        pi = permutation_importance(model, Xte, yte, n_repeats=8,
                                    random_state=config.RANDOM_STATE, scoring="roc_auc")
        order = np.argsort(pi.importances_mean)[::-1]
        importances = [{"feature": FEATURES[i],
                        "importance": round(float(pi.importances_mean[i]), 4)}
                       for i in order]
    except Exception:
        pass

    notes = [
        ("Split groups by person and by time: no individual appears on both sides, and "
         "the test half is later." if grouped else
         "Grouped split left too few test rows, so a plain temporal split was used — "
         "an individual may appear on both sides, which flatters the score slightly."),
        "Registry flags (rowdy sheeter / history sheet / active warrant) are excluded: they "
        "are undated snapshots that may post-date the verdict being predicted.",
        "Prior case and arrest counts are recomputed per row from records strictly before "
        "that case's own date.",
        f"Test-window conviction rate is {yte.mean():.1%} — the number any accuracy figure "
        "has to be read against.",
        "Outcome predictions are decision support for case preparation. They are not "
        "evidence and must never be presented to a court as such.",
    ]

    artifact = {"name": NAME, "estimator": model, "features": FEATURES,
                "numeric": NUMERIC, "categorical": CATEGORICAL,
                "_profile": monitoring.build_profile(
                    train_df, numeric=NUMERIC, categorical=CATEGORICAL, scores=prob)}

    if report is not None:
        report.add(
            NAME,
            algorithm=f"{best_label} with Platt calibration",
            task="binary classification (trial outcome)",
            target="conviction (vs acquittal) for a named accused",
            features=FEATURES,
            split={**splits.describe_split(train_df, test_df, cutoff),
                   "strategy": ("grouped by offender + temporal" if grouped
                                else "temporal only (grouping starved the test half)"),
                   "train_positive_rate": round(float(ytr.mean()), 4),
                   "test_positive_rate": round(float(yte.mean()), 4)},
            metrics=metrics,
            baselines=bl,
            notes=notes,
            extra={
                "cv_roc_auc": {k: (round(v, 4) if np.isfinite(v) else None)
                               for k, v in cv_scores.items()},
                "selected_model": best_label,
                "permutation_importance": importances,
                "fairness_slices": fairness,
            },
        )
    return artifact


def _fairness_slices(test_df, y, prob, pred):
    """Per-group performance. A model can look fine overall and fail a subgroup."""
    from sklearn.metrics import roc_auc_score

    out = []
    d = test_df.copy()
    d["_y"], d["_p"], d["_pred"] = y, prob, pred
    d["age_band"] = pd.cut(d["age"], [0, 25, 35, 50, 120],
                           labels=["<25", "25-34", "35-49", "50+"])
    d["gender_label"] = d["gender"].map({1: "Male", 2: "Female"}).fillna("Unknown")

    for col in ("age_band", "gender_label"):
        for name, g in d.groupby(col, observed=True):
            if len(g) < 25:
                continue
            row = {
                "slice": f"{col.replace('_label', '').replace('_band', '')} = {name}",
                "n": int(len(g)),
                "positive_rate": round(float(g["_y"].mean()), 4),
                "selection_rate": round(float(g["_pred"].mean()), 4),
                "accuracy": round(float((g["_pred"] == g["_y"]).mean()), 4),
            }
            if g["_y"].nunique() > 1:
                row["roc_auc"] = round(float(roc_auc_score(g["_y"], g["_p"])), 4)
            out.append(row)
    return out


def make_predictor(artifact):
    """accused/case feature dict -> calibrated probability of conviction."""
    if not artifact:
        return None
    est = artifact["estimator"]
    features = artifact["features"]

    def predict(row):
        d = {f: row.get(f, np.nan) for f in features}
        d.setdefault("crime_type", "Unknown")
        d.setdefault("district", "Unknown")
        return round(float(est.predict_proba(pd.DataFrame([d])[features])[0, 1]), 4)

    return predict
