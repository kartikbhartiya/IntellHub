"""Case progression — will this FIR reach a chargesheet within 180 days?

Two things were wrong with the previous version of this model.

**Target leakage.** It fed the investigating officer's `closure_rate`,
`cases_assigned` and `commendations` into a model predicting whether a case
closed. Those columns are aggregates *computed from case outcomes*, including
the outcome of the very case being scored — the label was sitting inside the
features. All three are banned in `config.BANNED_ALLOCATION_FEATURES` and the
model is rebuilt from case facts and officer attributes that exist before the
investigation starts.

**An undefined target.** `status == 'Closed'` is a snapshot with no clock. A case
registered in 2020 has had six years to close; one registered last month has had
weeks. Comparing them measures the calendar, not the investigation. Since a
random split scatters both across train and test, the old accuracy figure was
largely a reading of case age.

Here the target is explicitly time-boxed: **was a chargesheet filed within 180
days of registration?** ChargesheetDetails carries real dates, so the outcome is
observable, and cases too recent for the full 180 days to have elapsed are
excluded rather than counted as failures.
"""
import numpy as np
import pandas as pd

from .. import config, evaluate, monitoring, splits, baselines

NAME = "case_closure"
WINDOW_DAYS = 180

CRIME_TO_SPEC = {
    "Cyber Fraud (Jamtara style)": "Cybercrime",
    "Narcotics Distribution": "Narcotics",
    "Sandalwood Smuggling": "Forest Offences",
    "Murder": "Homicide",
    "Homicide": "Homicide",
    "Assault": "General",
    "Chain Snatching": "Property Crime",
    "House Break-in (Night)": "Property Crime",
    "Vehicle Theft": "Property Crime",
    "Extortion": "Organized Crime",
}

NUMERIC = ["severity_score", "fatalities", "injured", "witnesses", "accused_count",
           "officer_experience_years", "station_load", "spec_match",
           "max_accused_prior_cases", "mean_accused_prior_cases"]
CATEGORICAL = ["crime_type", "district", "rank", "time_of_day"]


def build_frame(data, window_days=None):
    """Cases old enough for the binary target to be observable.

    `window_days` is a parameter rather than a read of the module global because
    `pendency` needs the same feature engineering over the *full* case table. It
    used to get that by assigning `allocation.WINDOW_DAYS = 0`, calling in, and
    restoring in a `finally` — which is not reentrant and not thread-safe. Two
    overlapping calls interleave as:

        A: original=180, set 0
        B: original=0,   set 0      <- B captured A's temporary value
        A: finally -> restore 180
        B: finally -> restore 0     <- global left at 0 permanently

    after which every case-closure frame silently includes cases that cannot have
    an observable outcome, and the model trains on a target meaning "chargesheet
    within 0 days". The app serves 8 threads and warms up in a background thread,
    so that interleaving is reachable in production, and it fails silently.
    """
    window = WINDOW_DAYS if window_days is None else window_days
    cases = data["cases"]
    officers = data["officers"]
    chargesheets = data.get("chargesheets")
    if cases is None or cases.empty or chargesheets is None or chargesheets.empty:
        return pd.DataFrame()

    df = cases.dropna(subset=["ts"]).copy()
    df["registered_ts"] = df["registered_ts"].fillna(df["ts"])

    # --- observable outcome only: drop cases too recent to have had 180 days
    last_ts = df["registered_ts"].max()
    df = df[df["registered_ts"] <= last_ts - pd.Timedelta(days=window)]
    if df.empty:
        return df

    cs_first = chargesheets.groupby("case_key")["chargesheet_date"].min()
    df["chargesheet_date"] = df["case_key"].map(cs_first)
    elapsed = (df["chargesheet_date"] - df["registered_ts"]).dt.days
    df["y"] = ((elapsed >= 0) & (elapsed <= window)).astype(int)

    # --- officer attributes that exist independently of case outcomes
    if officers is not None and not officers.empty:
        off = officers.set_index("officer_key")
        df["appointment_date"] = df["officer_key"].map(off["appointment_date"])
        df["rank"] = df["officer_key"].map(off["rank"]).fillna("PC")
        df["specialization"] = df["officer_key"].map(off["specialization"]).fillna("General")
    else:
        df["appointment_date"] = pd.NaT
        df["rank"] = "PC"
        df["specialization"] = "General"

    exp = (df["registered_ts"] - df["appointment_date"]).dt.days / 365.25
    df["officer_experience_years"] = exp.fillna(exp.median()).clip(0, 40)
    df["spec_match"] = (df["specialization"] ==
                        df["crime_type"].map(CRIME_TO_SPEC).fillna("General")).astype(float)

    # Station workload in the 90 days before registration — a genuine, dated
    # capacity signal that does not depend on any case's outcome.
    df = df.sort_values("registered_ts")
    loads = []
    for station, g in df.groupby("police_station", sort=False):
        ts = g["registered_ts"]
        counts = [int(((ts >= t - pd.Timedelta(days=90)) & (ts < t)).sum()) for t in ts]
        loads.append(pd.Series(counts, index=g.index))
    df["station_load"] = pd.concat(loads).reindex(df.index).fillna(0) if loads else 0

    # Record of the accused named in the case, counted strictly before this case's
    # own date. A known repeat offender is easier to trace and charge than an
    # unidentified one, and the IO has this at registration.
    accused = data.get("accused")
    cases_all = data["cases"]
    if accused is not None and not accused.empty:
        ev = (accused.merge(cases_all[["case_key", "ts"]], on="case_key", how="inner")
              .dropna(subset=["ts"]).sort_values("ts"))
        ev["prior"] = ev.groupby("offender_id").cumcount()
        agg = ev.groupby("case_key")["prior"].agg(["max", "mean"])
        df["max_accused_prior_cases"] = df["case_key"].map(agg["max"]).fillna(0)
        df["mean_accused_prior_cases"] = df["case_key"].map(agg["mean"]).fillna(0)
    else:
        df["max_accused_prior_cases"] = 0
        df["mean_accused_prior_cases"] = 0

    return df


def _choose_threshold(y_true, prob, floor=0.02):
    """Operating point that maximises Matthews correlation on the given probabilities.

    Not raw accuracy: on a 76/24 split accuracy is maximised by predicting the
    majority class for everything, which is the degenerate rule this exists to avoid.

    MCC rather than balanced accuracy, decided on the training window: the two agree
    almost exactly on how well the model separates the classes (0.5939 vs 0.5944
    balanced accuracy out-of-fold), but balanced accuracy reached its maximum by
    flagging half of all cases where MCC reaches its own at 43%. This model exists to
    order a queue, and a flag that fires on one case in two orders very little. MCC
    is also the metric least distorted by imbalance, and is already reported here.

    The candidate grid is the probability quantiles rather than a fixed 0..1 sweep,
    so it adapts to whatever range the calibrated scores actually occupy.
    """
    from sklearn.metrics import matthews_corrcoef

    y_true = np.asarray(y_true)
    prob = np.asarray(prob, dtype=float)
    grid = np.unique(np.round(np.quantile(prob, np.linspace(0.01, 0.99, 197)), 4))
    grid = grid[grid > floor]
    if not len(grid):
        return 0.5
    best_t, best_score = 0.5, -2.0
    for t in grid:
        score = matthews_corrcoef(y_true, (prob >= t).astype(int))
        if score > best_score:
            best_t, best_score = float(t), score
    return best_t


def train(data, report=None):
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from sklearn.impute import SimpleImputer
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import (cross_val_score, cross_val_predict,
                                         StratifiedKFold)

    df = build_frame(data)
    if len(df) < 300 or df["y"].nunique() < 2:
        return None

    train_df, test_df, cutoff = splits.temporal_split(df, "registered_ts")
    if len(test_df) < 50 or train_df["y"].nunique() < 2 or test_df["y"].nunique() < 2:
        return None

    features = NUMERIC + CATEGORICAL
    Xtr, ytr = train_df[features], train_df["y"].to_numpy()
    Xte, yte = test_df[features], test_df["y"].to_numpy()

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
                max_depth=4, max_iter=300, learning_rate=0.05,
                random_state=config.RANDOM_STATE))]),
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

    # Where to cut the probability, decided on the training window only.
    #
    # A hardcoded 0.5 is wrong for this problem by construction: the model is
    # calibrated and the base rate is well under half, so a correct probability
    # rarely reaches 0.5 and almost every case gets labelled negative. That is how
    # the previous version reached ROC-AUC 0.646 with balanced accuracy 0.526 and a
    # class-1 recall of 0.10.
    #
    # `cross_val_predict` gives out-of-fold probabilities for the training rows, so
    # the threshold is never chosen on a prediction the model made about a row it had
    # already seen — and never on the test set, which would be fitting the decision
    # rule to the thing being measured.
    oof = cross_val_predict(model, Xtr, ytr, cv=cv, method="predict_proba")[:, 1]
    threshold = _choose_threshold(ytr, oof)

    model.fit(Xtr, ytr)
    prob = model.predict_proba(Xte)[:, 1]
    pred = (prob >= threshold).astype(int)
    metrics = evaluate.classification_metrics(yte, pred, y_prob=prob, labels=[0, 1])
    metrics["operating_threshold"] = round(float(threshold), 4)
    metrics["threshold_selected_on"] = ("out-of-fold training probabilities, "
                                       "maximising Matthews correlation")
    # What the old rule scored, kept alongside so the change is auditable rather
    # than a number that silently moved between runs.
    metrics["at_threshold_0.5"] = {
        k: v for k, v in
        evaluate.classification_metrics(yte, (prob >= 0.5).astype(int),
                                        y_prob=prob, labels=[0, 1]).items()
        if k in ("accuracy", "balanced_accuracy", "f1_macro", "mcc")
    }

    bl = [baselines.prior_probability_baseline(ytr, yte),
          baselines.majority_class_baseline(ytr, yte)]

    importances = []
    try:
        from sklearn.inspection import permutation_importance
        pi = permutation_importance(model, Xte, yte, n_repeats=8,
                                    random_state=config.RANDOM_STATE, scoring="roc_auc")
        order = np.argsort(pi.importances_mean)[::-1]
        importances = [{"feature": features[i],
                        "importance": round(float(pi.importances_mean[i]), 4)}
                       for i in order]
    except Exception:
        pass

    artifact = {"name": NAME, "estimator": model, "features": features,
                "numeric": NUMERIC, "categorical": CATEGORICAL,
                "window_days": WINDOW_DAYS,
                # Serving returns the probability, so nothing downstream breaks; this
                # is the operating point the published metrics were computed at.
                "threshold": round(float(threshold), 4),
                # Captured now, on the training window, because a baseline built
                # later from data that has already moved is not a baseline.
                "_profile": monitoring.build_profile(
                    train_df, numeric=NUMERIC, categorical=CATEGORICAL, scores=prob)}

    if report is not None:
        report.add(
            NAME,
            algorithm=f"{best_label} with Platt calibration",
            task="binary classification (case progression)",
            target=f"chargesheet filed within {WINDOW_DAYS} days of registration",
            features=features,
            split=splits.describe_split(train_df, test_df, cutoff, "registered_ts"),
            metrics=metrics,
            baselines=bl,
            notes=[
                f"Target is time-boxed to {WINDOW_DAYS} days using real chargesheet dates. "
                "Cases registered too recently for that window to have elapsed are excluded "
                "rather than being counted as failures.",
                "Officer closure_rate, cases_assigned and commendations are banned features: "
                "they are aggregates of case outcomes and contain the label. The previous "
                "version of this model used all three.",
                "Split is temporal on registration date, so the model is scored on cases "
                "registered after every case it learned from.",
                f"Base rate in the test window is {yte.mean():.1%}; read accuracy against that, "
                "not against 50%.",
                f"Decisions are taken at p >= {threshold:.3f}, not 0.5. The model is "
                "calibrated and the base rate is well under half, so a correct "
                "probability rarely reaches 0.5 — a 0.5 cut labelled almost every "
                "case negative and produced a class-1 recall of 0.10. The threshold "
                "is chosen on out-of-fold training probabilities and applied "
                "unchanged to the test window.",
                "Raw accuracy is LOWER at this threshold than at 0.5, and that is the "
                "intended direction: the majority-class baseline scores higher than "
                "both by never predicting the positive class at all. Balanced "
                "accuracy, F1-macro and MCC are the metrics that separate a useful "
                "model from that baseline here.",
                f"The threshold was selected under the training window's base rate "
                f"({ytr.mean():.1%}) and applied to a test window whose base rate is "
                f"{yte.mean():.1%}. That shift moves the whole score distribution up, "
                f"so the same cut flags {pred.mean():.1%} of test cases against "
                f"{(oof >= threshold).mean():.1%} of training cases. Re-tuning the "
                "threshold until the test fraction looked better would be fitting the "
                "decision rule to the evaluation; it is left as chosen and the "
                "discrepancy reported. Re-derive it on every retrain.",
                "The ranking, not this cut, is the operational artifact: read the "
                "lift table for how much of the positive class the top slice "
                "captures. The threshold exists so that the reported "
                "classification metrics reflect a decision rule that could actually "
                "be used.",
            ],
            extra={
                "cv_roc_auc": {k: (round(v, 4) if np.isfinite(v) else None)
                               for k, v in cv_scores.items()},
                "selected_model": best_label,
                "permutation_importance": importances,
                "banned_features": sorted(config.BANNED_ALLOCATION_FEATURES),
            },
        )
    return artifact


def make_predictor(artifact):
    """(case dict, officer dict) -> calibrated probability of timely chargesheet."""
    if not artifact:
        return None
    est = artifact["estimator"]
    features = artifact["features"]

    def predict(case, officer=None):
        officer = officer or {}
        appt = pd.to_datetime(officer.get("appointment_date"), errors="coerce")
        reg = pd.to_datetime(case.get("registered_ts") or case.get("timestamp"), errors="coerce")
        exp = (reg - appt).days / 365.25 if pd.notna(appt) and pd.notna(reg) else 8.0
        target_spec = CRIME_TO_SPEC.get(case.get("crime_type"), "General")
        row = {
            "severity_score": float(case.get("severity_score", 50) or 50),
            "fatalities": float(case.get("fatalities", 0) or 0),
            "injured": float(case.get("injured", 0) or 0),
            "witnesses": float(case.get("witnesses", 0) or 0),
            "accused_count": float(case.get("accused_count", 0) or 0),
            "officer_experience_years": float(np.clip(exp, 0, 40)),
            "station_load": float(case.get("station_load", 0) or 0),
            "spec_match": 1.0 if officer.get("specialization") == target_spec else 0.0,
            "crime_type": case.get("crime_type", "Unknown"),
            "district": case.get("district", "Unknown"),
            "rank": officer.get("rank", "PC"),
            "time_of_day": case.get("time_of_day", "Morning (7AM-10AM)"),
        }
        return round(float(est.predict_proba(pd.DataFrame([row])[features])[0, 1]), 4)

    return predict
