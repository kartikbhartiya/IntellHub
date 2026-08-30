"""Geospatial hotspot zones — DBSCAN over incident coordinates (haversine).

The previous version hard-coded `eps_km=45, min_samples=3` with no justification.
45 km is a very large radius for a "hotspot" — at that scale a cluster spans most
of a district and tells a patrol commander nothing actionable.

Here the parameters are **selected**, not assumed: a grid of (eps, min_samples)
is scored on silhouette (cluster separation), the share of points left as noise,
and whether the resulting zones are small enough to actually patrol. The chosen
setting and the full sweep are both written to the report, so the choice is
auditable rather than asserted.

DBSCAN has no ground truth to validate against, so these are internal validity
measures. They are reported as such — this is unsupervised structure discovery,
not prediction, and the report says so explicitly.
"""
import numpy as np
import pandas as pd

from .. import config, evaluate

NAME = "hotspot_clusters"
EARTH_KM = 6371.0088

EPS_GRID_KM = [1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 12.0, 20.0]
MIN_SAMPLES_GRID = [4, 6, 10, 15]
MAX_USEFUL_RADIUS_KM = 15.0   # beyond this a "zone" is not a patrollable area


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return EARTH_KM * 2 * np.arcsin(np.sqrt(a))


def _median_radius_km(lat, lng, labels):
    """Median 90th-percentile spread of the clusters, in km — the operational size."""
    radii = []
    for cl in set(labels.tolist()):
        if cl == -1:
            continue
        m = labels == cl
        clat, clng = float(lat[m].mean()), float(lng[m].mean())
        radii.append(float(np.percentile(_haversine_km(clat, clng, lat[m], lng[m]), 90)))
    return float(np.median(radii)) if radii else None


def _sweep(coords_rad, lat, lng):
    """Score every (eps, min_samples) pair so the choice can be defended."""
    from sklearn.cluster import DBSCAN

    results = []
    for eps_km in EPS_GRID_KM:
        for ms in MIN_SAMPLES_GRID:
            labels = DBSCAN(eps=eps_km / EARTH_KM, min_samples=ms,
                            metric="haversine").fit_predict(coords_rad)
            m = evaluate.clustering_metrics(coords_rad, labels)
            m.update({"eps_km": eps_km, "min_samples": ms,
                      "median_radius_km": _median_radius_km(lat, lng, labels)})
            if m["median_radius_km"] is not None:
                m["median_radius_km"] = round(m["median_radius_km"], 2)
            results.append(m)
    return results


MIN_ZONES = 4
MAX_ZONES = 60


def _pick(sweep):
    """Prefer well-separated clusters that leave most incidents assigned.

    Silhouette alone is not enough, and it fails in both directions. It rewards
    hundreds of four-incident micro-clusters, and it equally rewards merging a
    district's zones into one large, well-separated blob — a 27 km "hotspot" that
    scores 0.75 and cannot be patrolled. So the choice is constrained twice: the
    cluster count must be staffable, and the median zone must be small enough to
    actually send a unit to. Among the settings that satisfy both, the
    best-separated one wins.
    """
    def _ok(r):
        return (MIN_ZONES <= r.get("n_clusters", 0) <= MAX_ZONES
                and r.get("silhouette") is not None
                and r.get("noise_fraction", 1.0) <= 0.6
                and (r.get("median_radius_km") or 1e9) <= MAX_USEFUL_RADIUS_KM)

    viable = [r for r in sweep if _ok(r)]
    if not viable:   # relax patrollability before relaxing validity
        viable = [r for r in sweep
                  if MIN_ZONES <= r.get("n_clusters", 0) <= MAX_ZONES
                  and r.get("silhouette") is not None] or sweep
    return max(viable, key=lambda r: (r.get("silhouette") or -1) - 0.3 * r.get("noise_fraction", 1.0))


def train(data, report=None):
    from sklearn.cluster import DBSCAN

    cases = data["cases"]
    if cases is None or cases.empty:
        return None
    pts = cases.dropna(subset=["latitude", "longitude"])
    if len(pts) < 50:
        return None

    coords = np.radians(pts[["latitude", "longitude"]].to_numpy(dtype=float))
    lat = pts["latitude"].to_numpy(dtype=float)
    lng = pts["longitude"].to_numpy(dtype=float)
    sweep = _sweep(coords, lat, lng)
    chosen = _pick(sweep)

    labels = DBSCAN(eps=chosen["eps_km"] / EARTH_KM, min_samples=chosen["min_samples"],
                    metric="haversine").fit_predict(coords)
    pts = pts.assign(_cl=labels)

    zones = []
    for cl, g in pts[pts["_cl"] != -1].groupby("_cl"):
        clat, clng = float(g["latitude"].mean()), float(g["longitude"].mean())
        d = _haversine_km(clat, clng, g["latitude"].to_numpy(), g["longitude"].to_numpy())
        zones.append({
            "id": f"cluster-{int(cl)}",
            "lat": round(clat, 5), "lng": round(clng, 5),
            "cases": int(len(g)),
            "radius_km": round(float(np.percentile(d, 90)), 2),
            "dominant_crime": str(g["crime_type"].value_counts().idxmax()),
            "district": str(g["district"].value_counts().idxmax()),
            "avg_severity": round(float(g["severity_score"].mean()), 1),
            "open_cases": int((g["status"] != "Closed").sum()) if "status" in g else 0,
            "peak_time": str(g["time_of_day"].value_counts().idxmax())
            if "time_of_day" in g else None,
        })
    zones.sort(key=lambda c: c["cases"], reverse=True)
    zones = zones[:25]

    metrics = {k: v for k, v in chosen.items()}
    if zones:
        metrics["median_zone_radius_km"] = round(
            float(np.median([z["radius_km"] for z in zones])), 2)
        metrics["patrollable_zones"] = int(
            sum(1 for z in zones if z["radius_km"] <= MAX_USEFUL_RADIUS_KM))

    artifact = {"name": NAME, "zones": zones, "params": chosen, "sweep": sweep}

    if report is not None:
        report.add(
            NAME,
            algorithm="DBSCAN with haversine metric, parameters selected by silhouette sweep",
            task="unsupervised spatial clustering",
            target="none — structure discovery, not prediction",
            features=["incident latitude", "incident longitude"],
            split={"strategy": "not applicable — unsupervised",
                   "n_train": int(len(pts))},
            metrics=metrics,
            baselines=[],
            notes=[
                f"eps and min_samples chosen from a {len(sweep)}-point sweep scored on "
                "silhouette and noise fraction; the full sweep is included below so the "
                f"choice can be checked. Selected: eps={chosen['eps_km']} km, "
                f"min_samples={chosen['min_samples']}.",
                f"Cluster count is constrained to {MIN_ZONES}-{MAX_ZONES} zones. Silhouette "
                "on its own rewards hundreds of four-incident micro-clusters — a perfect "
                "score and an unusable patrol plan.",
                "Silhouette and Davies-Bouldin are internal validity measures. There is no "
                "ground-truth hotspot label, so these describe cluster structure — they are "
                "not accuracy and should not be read as such.",
                f"{metrics.get('patrollable_zones', 0)} of {len(zones)} zones fall within a "
                f"{MAX_USEFUL_RADIUS_KM} km radius, the rough limit of a patrollable area.",
                "Points marked as noise are genuinely dispersed incidents, not failures — "
                "forcing every incident into a zone would invent hotspots that do not exist.",
            ],
            extra={"parameter_sweep": sweep, "n_zones": len(zones)},
        )
    return artifact


def make_predictor(artifact):
    if not artifact:
        return None
    return lambda: artifact["zones"]
