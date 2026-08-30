"""Serving layer — what the Flask app calls at boot.

Load order for every model:

1. Try the persisted artifact. This is the fast path (a few hundred milliseconds
   for the whole set) and it guarantees the served model is byte-identical to the
   one the published metrics were measured on.
2. If the artifact is absent, or was built by a different scikit-learn version,
   retrain in-process from the *same* task module. Slower, but the two paths run
   identical code, so they cannot disagree about what a model is.

Metrics are never recomputed here. They are read from `model_registry.json`,
which is written only by an offline training run. That is deliberate: a metric
computed at request time against whatever data happens to be loaded is not a
measurement, and the number on the dashboard should be the number somebody can
reproduce by re-running the trainer.
"""
import time

from . import dataset, monitoring, registry
from .tasks import TASKS


def _obtain(name, data, log):
    """Artifact if usable, otherwise retrain. Returns (artifact, source)."""
    module = TASKS[name]
    artifact = registry.load_artifact(name)
    if artifact is not None:
        return artifact, "artifact"
    log(f"  {name}: no usable artifact — training in-process")
    try:
        artifact = module.train(data, None)
        return artifact, ("retrained" if artifact else "unavailable")
    except Exception as e:
        log(f"  {name}: training failed — {e}")
        return None, "failed"


def load_all(data=None, data_dir=None, log=print):
    """Load every model and its cached outputs. Never raises: a model that cannot
    be produced is reported as unavailable and the rest of the platform runs."""
    t0 = time.time()
    if data is None:
        data = dataset.load_canonical(data_dir)

    report = registry.read_report()
    out = {
        "data": data,
        "report": report,
        "sources": {},
        "artifacts": {},
        "predictors": {},
        "metrics": {},
        "outputs": {},
    }

    for name in TASKS:
        artifact, source = _obtain(name, data, log)
        out["sources"][name] = source
        out["artifacts"][name] = artifact
        if artifact is None:
            continue
        try:
            out["predictors"][name] = TASKS[name].make_predictor(artifact)
        except Exception as e:
            log(f"  {name}: predictor could not be built — {e}")
        if report and name in (report.get("models") or {}):
            entry = report["models"][name]
            out["metrics"][name] = entry

    # --- precomputed outputs the API serves directly
    fa = out["artifacts"].get("crime_forecast")
    out["outputs"]["forecast"] = fa["forecast"] if fa else {}

    ha = out["artifacts"].get("hotspot_clusters")
    out["outputs"]["clusters"] = ha["zones"] if ha else []

    ra = out["artifacts"].get("recidivism_risk")
    if ra:
        try:
            from .tasks import recidivism
            out["outputs"]["recidivism"] = recidivism.score_current_offenders(data, ra)
        except Exception as e:
            log(f"  recidivism scoring failed — {e}")
            out["outputs"]["recidivism"] = {"scores": {}, "top": []}
    else:
        out["outputs"]["recidivism"] = {"scores": {}, "top": []}

    pa = out["artifacts"].get("case_pendency")
    if pa:
        try:
            from .tasks import pendency
            out["outputs"]["pendency"] = pendency.score_open_docket(data, pa)
        except Exception as e:
            log(f"  pendency docket scoring failed — {e}")
            out["outputs"]["pendency"] = {"cases": [], "summary": {},
                                          "by_district": [], "by_station": []}
    else:
        out["outputs"]["pendency"] = {"cases": [], "summary": {},
                                      "by_district": [], "by_station": []}

    counts = {}
    for s in out["sources"].values():
        counts[s] = counts.get(s, 0) + 1
    log(f"ML layer ready in {time.time() - t0:.1f}s — " +
        ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    if report is None:
        log("  no model_registry.json found — run `python -m ml.train` to publish metrics")
    return out


# ---------------------------------------------------------------- convenience wrappers
def conviction_outlook(offender_id, data, predictor, artifact):
    """Conviction probability for an offender, evaluated on their most recent case.

    The model scores an (accused, case) pair, not a person — the strength of a
    prosecution depends on the case. Asking it for a bare person-level number
    would be a category error, so this picks the offender's latest case and says
    which case the number refers to.
    """
    if predictor is None or artifact is None:
        return None
    try:
        import numpy as np
        import pandas as pd

        accused, cases = data.get("accused"), data.get("cases")
        if accused is None or accused.empty or cases is None or cases.empty:
            return None
        mine = accused[accused["offender_id"].astype(str) == str(offender_id)]
        if mine.empty:
            return None

        ev = mine.merge(cases, on="case_key", how="inner").dropna(subset=["ts"])
        if ev.empty:
            return None
        ev = ev.sort_values("ts")
        latest = ev.iloc[-1]
        prior = ev.iloc[:-1]

        arrests = data.get("arrests")
        arrest_delay = np.nan
        if arrests is not None and not arrests.empty:
            a = arrests[arrests["case_key"] == latest["case_key"]]["arrest_date"]
            if not a.empty and pd.notna(latest.get("registered_ts")):
                arrest_delay = (a.min() - latest["registered_ts"]).days
        cs = data.get("chargesheets")
        cs_delay = np.nan
        if cs is not None and not cs.empty:
            c = cs[cs["case_key"] == latest["case_key"]]["chargesheet_date"]
            if not c.empty and pd.notna(latest.get("registered_ts")):
                cs_delay = (c.min() - latest["registered_ts"]).days

        row = {
            "age": latest.get("age"),
            "gender": latest.get("gender", 1),
            "n_prior_cases": len(prior),
            "n_prior_arrests": int(len(arrests[arrests["offender_id"].astype(str) == str(offender_id)]))
            if arrests is not None and not arrests.empty else 0,
            "days_since_first_case": int((latest["ts"] - ev.iloc[0]["ts"]).days),
            "n_prior_districts": int(prior["district"].nunique()) if len(prior) else 0,
            "n_co_accused": int(max(0, (latest.get("accused_count") or 1) - 1)),
            "severity_score": latest.get("severity_score"),
            "fatalities": latest.get("fatalities"),
            "injured": latest.get("injured"),
            "witnesses": latest.get("witnesses"),
            "arrest_delay_days": arrest_delay,
            "chargesheet_delay_days": cs_delay,
            "crime_type": latest.get("crime_type"),
            "district": latest.get("district"),
        }
        probability = predictor(row)
        # Every score handed to a person is recorded. Without this, "was that
        # prediction right?" is unanswerable and no feedback loop can be built on
        # top of it later.
        monitoring.log_prediction("conviction_outcome", offender_id, probability,
                                  subject_type="offender",
                                  extra={"case_id": str(latest.get("case_id"))})
        # A challenge that changed nothing about what the officer sees would be a
        # complaints box, not a control. The score is not suppressed — that is the
        # deploying authority's decision, not this function's — but it never travels
        # without the fact that it is disputed.
        contested = False
        try:
            import contest
            contested = contest.is_contested(offender_id)
        except Exception:
            pass

        return {
            "probability": probability,
            "case_id": str(latest.get("case_id")),
            "crime_type": str(latest.get("crime_type")),
            "basis": "most recent case naming this offender",
            "contested": contested,
            "contested_note": ("This assessment has been formally challenged and is "
                               "under review." if contested else None),
        }
    except Exception as e:
        # Returning None made a model failure look identical to "this offender has
        # no cases" on the dossier — the same null, two very different meanings.
        import logging
        logging.getLogger("ksp.ml").warning(
            "conviction outlook unavailable for %s: %s", offender_id, e)
        return None
