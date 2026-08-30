"""One module per model.

Every task module exposes the same two functions so `train.py` and `main.py`
can treat them uniformly:

    train(data, report) -> artifact        fits, evaluates, records into the report
    make_predictor(artifact) -> callable   rebuilds the live scoring function

`artifact` is a plain dict that joblib can persist. It always carries an `_env`
key stamped by the registry so a mismatched runtime is detected on load rather
than producing silently wrong scores.
"""
from . import narrative, forecast, recidivism, conviction, allocation, hotspots, pendency

TASKS = {
    "narrative_classifier": narrative,
    "crime_forecast": forecast,
    "recidivism_risk": recidivism,
    "conviction_outcome": conviction,
    "case_closure": allocation,
    "hotspot_clusters": hotspots,
    "case_pendency": pendency,
}

__all__ = ["TASKS", "narrative", "forecast", "recidivism", "conviction",
           "allocation", "hotspots", "pendency"]
