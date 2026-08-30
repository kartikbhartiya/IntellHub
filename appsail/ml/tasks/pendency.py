"""Case pendency — how long until this FIR reaches a chargesheet?

This is the survival-analysis counterpart to `allocation.py`, and it exists
because the binary model has two limits that no amount of tuning removes.

**It discards the cases you most need scored.** To ask "chargesheet within 180
days?" honestly, `allocation.py` must drop every case registered in the final 180
days of the corpus — they have not had time to answer. Those are precisely the
cases sitting open on a desk today. The model is structurally incapable of
scoring the live docket.

**It answers one question forever.** SCRB does not have a single 180-day clock.
Different offence classes carry different statutory and administrative
expectations, and a Superintendent wants the 60-day figure while a court-liaison
cell wants 180. Three binary models would be three chances to disagree.

Modelling time-to-event fixes both. A case open 40 days with no chargesheet is
**right-censored**, not a failure: it tells us "survived at least 40 days", and
survival analysis is built to use exactly that. One fitted model then answers
every horizon off a single curve.

Construction
------------
Discrete-time survival via person-period expansion (see `ml/survival.py`) —
sklearn-only, no new dependency. Each case becomes one row per 30-day interval it
survived into; a classifier on those rows *is* an estimator of the discrete
hazard h(k | x); chaining gives S(t | x).

Guard rails carried over from `allocation.py`, because they apply identically:

* The banned officer aggregates (`closure_rate`, `cases_assigned`,
  `commendations`) stay banned — they are computed from case outcomes and
  contain the label.
* Split is temporal on registration date.
* Station workload is measured in the 90 days *before* registration.

Baseline: the **Kaplan-Meier** curve. It is the covariate-free answer — what you
can say about a case knowing only that it exists — and it is the reference a
survival model has to beat to have learned anything at all.
"""
import numpy as np
import pandas as pd

from .. import monitoring, config, splits, survival
from . import allocation

NAME = "case_pendency"

INTERVAL_DAYS = 30
N_INTERVALS = 12                      # 360-day observation grid
HORIZONS = [60, 90, 180, 360]         # days at which breach risk is reported

NUMERIC = allocation.NUMERIC
CATEGORICAL = allocation.CATEGORICAL
FEATURES = NUMERIC + CATEGORICAL


def build_frame(data):
    """Cases with a duration and a censoring flag.

    Reuses `allocation.build_frame` for feature construction — the features are
    the same case facts — but rebuilds the *target*, and critically does not
    inherit its 180-day exclusion. Every registered case gets a row here.
    """
    cases = data["cases"]
    chargesheets = data.get("chargesheets")
    if cases is None or cases.empty or chargesheets is None or chargesheets.empty:
        return pd.DataFrame()

    # `allocation.build_frame` drops recent cases to make its binary target
    # observable. Re-run its feature engineering over the *full* case table by
    # temporarily neutralising that exclusion.
    full = _features_without_exclusion(data)
    if full.empty:
        return full

    cs_first = chargesheets.groupby("case_key")["chargesheet_date"].min()
    full["chargesheet_date"] = full["case_key"].map(cs_first)

    # Observation ends at the latest registration date in the corpus — the moment
    # the extract was taken. Anything still open then is censored there.
    observed_until = full["registered_ts"].max()

    elapsed = (full["chargesheet_date"] - full["registered_ts"]).dt.days
    has_event = elapsed.notna() & (elapsed >= 0)

    full["duration_days"] = np.where(
        has_event,
        elapsed,
        (observed_until - full["registered_ts"]).dt.days,
    ).astype(float)
    full["event"] = has_event.astype(int)

    # A zero/negative follow-up carries no information either way.
    full = full[full["duration_days"] > 0]
    full["_observed_until"] = observed_until
    return full


def _features_without_exclusion(data):
    """Run allocation's feature engineering over every case, not just the aged ones.

    `allocation.build_frame` deliberately truncates to cases old enough for its
    binary label. That truncation is the thing this model exists to avoid, so the
    window is passed in as zero. This previously assigned to the module global
    `allocation.WINDOW_DAYS` and restored it in a `finally`, which raced with any
    concurrent caller — see the note in allocation.build_frame.
    """
    return allocation.build_frame(data, window_days=0)


def _fit_hazard(train_df, model_kind="Gradient Boosting"):
    """Fit the discrete-hazard classifier on person-period rows."""
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from sklearn.impute import SimpleImputer

    row_idx, interval, y = survival.expand_person_period(
        train_df["duration_days"].to_numpy(),
        train_df["event"].to_numpy(),
        INTERVAL_DAYS, N_INTERVALS)
    if not len(y) or len(np.unique(y)) < 2:
        return None, None

    X = train_df.iloc[row_idx][FEATURES].reset_index(drop=True)
    # The interval index is itself a covariate: it is what lets the model learn a
    # baseline hazard shape (chargesheets cluster, they do not arrive uniformly).
    X["interval"] = interval

    numeric = NUMERIC + ["interval"]
    # sparse_output=False: with 31 districts the one-hot block tips the
    # ColumnTransformer over its sparse_threshold, and HistGradientBoosting
    # rejects sparse input. Left sparse, the gradient-boosting candidate raises
    # inside cross_val_score, gets scored NaN by the except-branch below and is
    # silently dropped from selection — so the "best model" would be chosen from
    # a field of one without anything saying so.
    pre = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")),
                          ("scale", StandardScaler())]), numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=10,
                              sparse_output=False), CATEGORICAL),
    ])
    if model_kind == "Logistic Regression":
        clf = LogisticRegression(max_iter=2000, random_state=config.RANDOM_STATE)
    else:
        clf = HistGradientBoostingClassifier(
            max_depth=4, max_iter=250, learning_rate=0.05,
            l2_regularization=1.0, random_state=config.RANDOM_STATE)
    pipe = Pipeline([("pre", pre), ("clf", clf)])
    pipe.fit(X, y)
    return pipe, numeric + CATEGORICAL + ["interval"]


def _predict_hazard(pipe, frame):
    """Discrete-hazard matrix (n_cases, N_INTERVALS) for every row of `frame`."""
    n = len(frame)
    if n == 0:
        return np.zeros((0, N_INTERVALS))
    base = frame[FEATURES].reset_index(drop=True)
    blocks = []
    for k in range(1, N_INTERVALS + 1):
        b = base.copy()
        b["interval"] = k
        blocks.append(b)
    stacked = pd.concat(blocks, ignore_index=True)
    return pipe.predict_proba(stacked)[:, 1].reshape(N_INTERVALS, n).T


def _predict_survival(pipe, frame):
    """Survival matrix (n_cases, N_INTERVALS) for every row of `frame`."""
    hz = _predict_hazard(pipe, frame)
    if hz.size == 0:
        return np.zeros((0, N_INTERVALS))
    return survival.survival_from_hazard(hz)


def _rmst(surv_matrix):
    """Restricted mean survival time in days — area under the curve to the horizon.

    Used as the risk ordering for the C-index (higher risk = shorter RMST) and as
    the headline "expected days open, capped at 360" shown in the UI.
    """
    if surv_matrix.size == 0:
        return np.zeros(0)
    return INTERVAL_DAYS * surv_matrix.sum(axis=1)


def train(data, report=None):
    from sklearn.metrics import roc_auc_score

    frame = build_frame(data)
    if len(frame) < 300 or frame["event"].nunique() < 2:
        return None

    train_df, test_df, cutoff = splits.temporal_split(frame, "registered_ts")
    if len(test_df) < 60 or train_df["event"].nunique() < 2:
        return None
    train_df = train_df.reset_index(drop=True)
    test_df = test_df.reset_index(drop=True)

    # --- candidate selection on the training window only
    fitted = {}
    for kind in ("Logistic Regression", "Gradient Boosting"):
        pipe, _ = _fit_hazard(train_df, kind)
        if pipe is None:
            continue
        # Score candidates by C-index on a held-back tail of the *training*
        # window, so the test window stays untouched until the final read.
        n_hold = max(50, int(len(train_df) * config.VALID_FRACTION))
        inner_tr = train_df.iloc[:-n_hold]
        inner_va = train_df.iloc[-n_hold:]
        if len(inner_tr) < 200 or inner_va["event"].nunique() < 2:
            fitted[kind] = (pipe, None)
            continue
        inner_pipe, _ = _fit_hazard(inner_tr, kind)
        if inner_pipe is None:
            fitted[kind] = (pipe, None)
            continue
        s_va = _predict_survival(inner_pipe, inner_va)
        c_va = survival.concordance_index(
            inner_va["duration_days"].to_numpy(), inner_va["event"].to_numpy(),
            -_rmst(s_va))
        fitted[kind] = (pipe, c_va)

    if not fitted:
        return None
    valid_scores = {k: v[1] for k, v in fitted.items()}
    best_label = max(fitted, key=lambda k: (fitted[k][1] if fitted[k][1] is not None else -1))
    model = fitted[best_label][0]

    # --- final read on the untouched test window
    surv_te = _predict_survival(model, test_df)
    dur_te = test_df["duration_days"].to_numpy()
    ev_te = test_df["event"].to_numpy()
    risk_te = -_rmst(surv_te)

    c_index = survival.concordance_index(dur_te, ev_te, risk_te)
    grid = np.array([INTERVAL_DAYS * (k + 1) for k in range(N_INTERVALS)], dtype=float)
    ibs = survival.integrated_brier(dur_te, ev_te, surv_te, grid)

    # Per-horizon discrimination, restricted to rows that can answer that horizon.
    per_horizon = []
    for h in HORIZONS:
        col = min(int(np.ceil(h / INTERVAL_DAYS)) - 1, N_INTERVALS - 1)
        mask, y = survival.horizon_labels(dur_te, ev_te, h)
        if mask.sum() < 30 or len(np.unique(y)) < 2:
            continue
        p_event = 1.0 - surv_te[mask, col]
        per_horizon.append({
            "horizon_days": h,
            "roc_auc": round(float(roc_auc_score(y, p_event)), 4),
            "brier": round(float(np.mean((p_event - y) ** 2)), 4),
            "n_evaluable": int(mask.sum()),
            "observed_charge_rate": round(float(y.mean()), 4),
        })

    # --- Kaplan-Meier baseline: the covariate-free curve
    km_train = survival.kaplan_meier(
        train_df["duration_days"].to_numpy(), train_df["event"].to_numpy(), grid=grid)
    km_surv = np.tile(np.asarray(km_train["survival"], dtype=float), (len(test_df), 1))
    km_ibs = survival.integrated_brier(dur_te, ev_te, km_surv, grid)
    km_med = survival.km_median(train_df["duration_days"].to_numpy(),
                               train_df["event"].to_numpy())

    baselines_out = [{
        "strategy": "Kaplan-Meier (no covariates)",
        "c_index": 0.5,
        "integrated_brier_score": km_ibs.get("integrated_brier_score"),
        "median_days_to_chargesheet": km_med,
        "note": ("The marginal survival curve — identical prediction for every case, "
                 "so it cannot rank and scores exactly 0.5 by construction. It is "
                 "still the right Brier reference: a covariate model that does not "
                 "beat its integrated Brier score has learned nothing useful."),
    }]

    censored_n = int((ev_te == 0).sum())
    # How much data the binary formulation would have thrown away.
    discarded_by_binary = int((frame["registered_ts"] >
                               frame["_observed_until"].iloc[0] -
                               pd.Timedelta(days=allocation.WINDOW_DAYS)).sum())

    artifact = {
        "name": NAME, "estimator": model, "features": FEATURES,
        "numeric": NUMERIC, "categorical": CATEGORICAL,
        "interval_days": INTERVAL_DAYS, "n_intervals": N_INTERVALS,
        "horizons": HORIZONS,
        "km_baseline": {"times": km_train["times"], "survival": km_train["survival"]},
        # Reference distribution for drift, captured on the training window.
        "_profile": monitoring.build_profile(
            train_df, numeric=NUMERIC, categorical=CATEGORICAL),
    }

    if report is not None:
        metrics = {
            "c_index": c_index,
            "integrated_brier_score": ibs.get("integrated_brier_score"),
            "n_test": int(len(test_df)),
            "n_test_censored": censored_n,
            "censoring_rate": round(float(censored_n / max(1, len(test_df))), 4),
            "per_horizon": per_horizon,
        }
        report.add(
            NAME,
            algorithm=f"Discrete-time survival ({best_label} hazard, {INTERVAL_DAYS}-day intervals)",
            task="time-to-event / survival analysis (case pendency)",
            target="days from FIR registration to first chargesheet, right-censored at extract date",
            features=FEATURES + ["interval"],
            split={**splits.describe_split(train_df, test_df, cutoff, "registered_ts"),
                   "train_event_rate": round(float(train_df["event"].mean()), 4),
                   "test_event_rate": round(float(test_df["event"].mean()), 4)},
            metrics=metrics,
            baselines=baselines_out,
            notes=[
                "Right-censoring is modelled, not discarded. A case still open at the "
                "extract date contributes every interval it survived and is never "
                "scored as a failure — which is what lets this model score the live "
                "docket, including cases registered days ago.",
                f"The binary case_closure model must exclude the {discarded_by_binary} "
                f"cases registered in the last {allocation.WINDOW_DAYS} days to keep its "
                "target observable. This model uses them.",
                f"{censored_n} of {len(test_df)} test cases ({censored_n / max(1, len(test_df)):.0%}) "
                "are censored. A naive 'fraction charged so far' statistic would count "
                "every one as a failure and understate performance across the board.",
                "One fitted model answers every horizon: the 60/90/180/360-day figures "
                "are read off the same survival curve rather than coming from separate "
                "models that could contradict each other.",
                "Per-horizon metrics use only cases whose outcome at that horizon is "
                "genuinely observable; the count is published as n_evaluable so a "
                "figure computed on few rows cannot masquerade as a stable one.",
                "Integrated Brier is the censoring-restricted estimate rather than the "
                "IPCW-weighted one — the conservative choice.",
                "Pendency risk is a workload triage signal. It reflects how comparable "
                "past investigations progressed and must never be read as a judgement "
                "of an officer or of any accused person.",
            ],
            extra={
                "selected_model": best_label,
                "validation_c_index": {k: v for k, v in valid_scores.items()},
                "interval_days": INTERVAL_DAYS,
                "n_intervals": N_INTERVALS,
                "brier_by_horizon": ibs.get("per_horizon", []),
                "kaplan_meier_curve": {
                    "times": [float(t) for t in km_train["times"]],
                    "survival": [round(float(s), 4) for s in km_train["survival"]],
                },
                "banned_features": sorted(config.BANNED_ALLOCATION_FEATURES),
                "cases_recovered_from_censoring": discarded_by_binary,
            },
        )
    return artifact


def make_predictor(artifact):
    """case dict -> survival curve + breach probability at each SLA horizon."""
    if not artifact:
        return None
    est = artifact["estimator"]
    features = artifact["features"]
    interval_days = artifact.get("interval_days", INTERVAL_DAYS)
    n_intervals = artifact.get("n_intervals", N_INTERVALS)
    horizons = artifact.get("horizons", HORIZONS)

    def predict(case, officer=None, days_open=None):
        officer = officer or {}
        appt = pd.to_datetime(officer.get("appointment_date"), errors="coerce")
        reg = pd.to_datetime(case.get("registered_ts") or case.get("timestamp"),
                             errors="coerce")
        exp = (reg - appt).days / 365.25 if pd.notna(appt) and pd.notna(reg) else 8.0
        target_spec = allocation.CRIME_TO_SPEC.get(case.get("crime_type"), "General")
        row = {
            "severity_score": float(case.get("severity_score", 50) or 50),
            "fatalities": float(case.get("fatalities", 0) or 0),
            "injured": float(case.get("injured", 0) or 0),
            "witnesses": float(case.get("witnesses", 0) or 0),
            "accused_count": float(case.get("accused_count", 0) or 0),
            "officer_experience_years": float(np.clip(exp, 0, 40)),
            "station_load": float(case.get("station_load", 0) or 0),
            "spec_match": 1.0 if officer.get("specialization") == target_spec else 0.0,
            "max_accused_prior_cases": float(case.get("max_accused_prior_cases", 0) or 0),
            "mean_accused_prior_cases": float(case.get("mean_accused_prior_cases", 0) or 0),
            "crime_type": case.get("crime_type", "Unknown"),
            "district": case.get("district", "Unknown"),
            "rank": officer.get("rank", "PC"),
            "time_of_day": case.get("time_of_day", "Morning (7AM-10AM)"),
        }
        base = pd.DataFrame([row])[features]
        blocks = []
        for k in range(1, n_intervals + 1):
            b = base.copy()
            b["interval"] = k
            blocks.append(b)
        hz = est.predict_proba(pd.concat(blocks, ignore_index=True))[:, 1]
        surv = np.cumprod(1.0 - np.clip(hz, 1e-9, 1 - 1e-9))

        out = {
            "curve": [{"day": int(interval_days * (k + 1)),
                       "survival": round(float(surv[k]), 4)}
                      for k in range(n_intervals)],
            "expected_days_open": round(float(interval_days * surv.sum()), 1),
            "breach_risk": {},
        }
        for h in horizons:
            col = min(int(np.ceil(h / interval_days)) - 1, n_intervals - 1)
            # P(still open at h) — the SLA breach probability.
            out["breach_risk"][f"{h}d"] = round(float(surv[col]), 4)

        # Conditional refresh for a case already open: renormalise the curve on
        # the intervals it has already survived. A case open 100 days should not
        # be quoted the same 90-day risk as one registered this morning.
        if days_open is not None and days_open > 0:
            passed = min(int(np.ceil(days_open / interval_days)), n_intervals) - 1
            passed = max(passed, 0)
            s0 = float(surv[passed]) if surv[passed] > 1e-9 else 1e-9
            out["conditional_breach_risk"] = {
                f"{h}d": round(float(min(1.0, surv[min(int(np.ceil(h / interval_days)) - 1,
                                                       n_intervals - 1)] / s0)), 4)
                for h in horizons
            }
            out["days_open"] = int(days_open)
        return out

    return predict


# ---------------------------------------------------------------- live docket
def score_open_docket(data, artifact, top_n=200):
    """Score the open docket — the capability the binary model cannot provide.

    "Open" means no chargesheet recorded as of the extract date: exactly the rows
    that are right-censored in training.

    **The docket is split in two, and the split matters.** The model observes a
    360-day grid. A case that has already been open longer than that sits outside
    the model's support entirely — every comparable case in the training data had
    already resolved or been abandoned, so a "risk score" for it would be
    extrapolation dressed up as a prediction. Those cases are reported as a
    **structural backlog** with counts and ages only, and explicitly not scored.

    Cases inside the grid get a genuine conditional forecast: the probability the
    case is still open at each SLA horizon *given it has already survived to
    today*. A case open 150 days and one registered this morning must not be
    quoted the same 180-day risk, and conditioning is what separates them.

    Refusing to score the backlog is the point, not a limitation. A dashboard
    that prints "97% risk" against a six-year-old file is not telling an officer
    anything they did not know, and it launders an administrative fact as a model
    output.
    """
    empty = {"cases": [], "summary": {}, "by_district": [], "by_station": [],
             "backlog": {}, "km_curve": {}}
    if not artifact:
        return empty
    try:
        frame = build_frame(data)
    except Exception:
        return empty
    if frame.empty:
        return empty

    open_df = frame[frame["event"] == 0].copy()
    if open_df.empty:
        return empty

    interval_days = artifact.get("interval_days", INTERVAL_DAYS)
    n_intervals = artifact.get("n_intervals", N_INTERVALS)
    horizons = artifact.get("horizons", HORIZONS)
    grid_max = interval_days * n_intervals

    # --- partition: forecastable vs beyond the model's observation grid
    within = open_df[open_df["duration_days"] <= grid_max].reset_index(drop=True)
    beyond = open_df[open_df["duration_days"] > grid_max]

    backlog = {
        "count": int(len(beyond)),
        "share_of_open": round(float(len(beyond) / max(1, len(open_df))), 4),
        "median_days_open": (round(float(beyond["duration_days"].median()), 1)
                             if len(beyond) else None),
        "oldest_days_open": (int(beyond["duration_days"].max()) if len(beyond) else None),
        "by_district": (beyond.groupby("district").size()
                        .sort_values(ascending=False).astype(int).to_dict()
                        if len(beyond) else {}),
        "note": (f"Open longer than the model's {grid_max}-day observation grid. These are "
                 "deliberately NOT scored: no comparable training case was still "
                 "unresolved at this age, so any probability shown here would be "
                 "extrapolation rather than a measurement. They need administrative "
                 "disposal review, not a forecast."),
    }

    if within.empty:
        return {**empty, "backlog": backlog,
                "km_curve": artifact.get("km_baseline", {}),
                "summary": {"open_cases": int(len(open_df)), "forecastable": 0,
                            "backlog": backlog["count"]}}

    est = artifact["estimator"]
    hazard = _predict_hazard(est, within)
    surv = survival.survival_from_hazard(hazard)
    days_open = within["duration_days"].to_numpy()

    # Conditional survival, evaluated at the *exact* elapsed day rather than at
    # the enclosing interval boundary. Grid-only arithmetic makes a case open 35
    # days look certain to still be open on day 60 simply because both fall in
    # the same interval; interpolating removes that false certainty.
    s_now = np.maximum(survival.survival_at(hazard, days_open, interval_days), 1e-9)
    passed_col = np.clip(np.ceil(np.maximum(days_open, 1) / interval_days).astype(int) - 1,
                         0, n_intervals - 1)

    # Expected *remaining* days open: the area under the conditional survival
    # curve from today to the grid edge. This is restricted mean residual life —
    # "if nothing changes, this file stays on the desk about N more days" — and
    # unlike a breach probability it stays informative for every case age.
    cols = np.arange(n_intervals)[None, :]
    future = cols >= passed_col[:, None]
    extra_days = interval_days * ((surv / s_now[:, None]) * future).sum(axis=1)

    rows = []
    for i in range(len(within)):
        r = within.iloc[i]
        cond, elapsed = {}, {}
        for h in horizons:
            if days_open[i] >= h:
                # Already past this horizon — observed fact, not a forecast.
                elapsed[f"{h}d"] = True
                cond[f"{h}d"] = 1.0
            else:
                elapsed[f"{h}d"] = False
                s_h = float(survival.survival_at(
                    hazard[i:i + 1], [float(h)], interval_days)[0])
                cond[f"{h}d"] = round(float(min(1.0, s_h / s_now[i])), 4)
        rows.append({
            "case_id": str(r.get("case_id")),
            "district": str(r.get("district")),
            "police_station": str(r.get("police_station")),
            "crime_type": str(r.get("crime_type")),
            "registered": (str(r["registered_ts"].date())
                           if pd.notna(r.get("registered_ts")) else None),
            "days_open": int(days_open[i]),
            "severity_score": int(r.get("severity_score") or 0),
            "expected_days_open": round(float(interval_days * surv[i].sum()), 1),
            "expected_additional_days": round(float(extra_days[i]), 1),
            "breach_risk": cond,
            "horizon_elapsed": elapsed,
            "risk": next((cond[f"{h}d"] for h in horizons if not elapsed[f"{h}d"]), 1.0),
            "next_horizon_days": next((h for h in horizons if not elapsed[f"{h}d"]), None),
            "already_breached_90d": bool(days_open[i] > 90),
        })

    # Rank on expected *additional* days, not on breach probability. A case open
    # 359 days scores ~1.0 against its 360-day horizon simply because one day
    # remains — true, but useless for triage, and it buries the cases an officer
    # could still act on. Remaining-time discriminates across the whole docket
    # and is what a station actually schedules against.
    rows.sort(key=lambda x: (-x["expected_additional_days"], -x["days_open"]))

    def _rollup(key, limit=None):
        agg = {}
        for r in rows:
            a = agg.setdefault(r[key], {"name": r[key], "open_cases": 0,
                                        "breached_90d": 0, "sum_risk": 0.0, "sum_days": 0})
            a["open_cases"] += 1
            a["breached_90d"] += int(r["already_breached_90d"])
            a["sum_risk"] += r["risk"]
            a["sum_days"] += r["days_open"]
        out = []
        for a in agg.values():
            n = max(1, a["open_cases"])
            out.append({
                "name": a["name"],
                "open_cases": a["open_cases"],
                "breached_90d": a["breached_90d"],
                "breach_rate_90d": round(a["breached_90d"] / n, 4),
                "mean_risk": round(a["sum_risk"] / n, 4),
                "mean_days_open": round(a["sum_days"] / n, 1),
            })
        out.sort(key=lambda x: -x["mean_risk"])
        return out[:limit] if limit else out

    total_open = int(len(open_df))
    breached = sum(1 for r in rows if r["already_breached_90d"])
    summary = {
        "open_cases": total_open,
        "forecastable": int(len(rows)),
        "backlog": backlog["count"],
        "already_breached_90d": breached,
        "breach_rate_90d": round(breached / max(1, len(rows)), 4),
        "mean_days_open": round(float(np.mean(days_open)), 1),
        "median_days_open": round(float(np.median(days_open)), 1),
        "high_risk_cases": int(sum(1 for r in rows if r["risk"] >= 0.7)),
        "grid_max_days": grid_max,
        "note": ("Risk is conditional on time already elapsed and is quoted only for "
                 f"cases inside the model's {grid_max}-day grid. Horizons already "
                 "passed are reported as observed fact, not forecast."),
    }

    return {
        "cases": rows[:top_n],
        "summary": summary,
        "by_district": _rollup("district"),
        "by_station": _rollup("police_station", limit=20),
        "backlog": backlog,
        "km_curve": artifact.get("km_baseline", {}),
    }
