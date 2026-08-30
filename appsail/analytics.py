"""
KSP IntelHub — advanced analytics & ML layer.

Pure, self-contained functions that take the loaded dataframes / knowledge graph and
return JSON-serialisable intelligence. Kept out of main.py so the model code is
independently testable and the request layer stays thin. Everything degrades gracefully:
any model that can't fit (missing columns, too little data) returns an empty result
instead of raising, so a partial dataset never takes the API down.

What lives here, and what does not
----------------------------------
This module holds the **graph-derived** intelligence — the parts that depend on the
NetworkX knowledge graph built at load time rather than on a fitted model:

* Adamic-Adar link prediction over the co-offender network
* Louvain community detection (syndicates), on the offender projection
* Socio-economic correlation
* Case↔suspect matching and per-suspect correlation reports

Everything supervised — forecasting, hotspot clustering, the narrative classifier,
recidivism, conviction, case progression, pendency — lives in `ml/tasks/` and is
trained offline with temporal validation. Seven functions here reimplemented those
models and were deleted: they had become a closed cluster that nothing reachable
called, and an untested second implementation alongside a tested one is how the two
diverge and someone edits the wrong copy.
"""
import sys
import os

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENDOR_DIR = os.path.join(_CURRENT_DIR, '_vendor')
if os.path.exists(_VENDOR_DIR):
    if sys.platform != 'win32':
        if _VENDOR_DIR not in sys.path:
            sys.path.insert(0, _VENDOR_DIR)
    else:
        if _VENDOR_DIR not in sys.path:
            sys.path.append(_VENDOR_DIR)

import logging
import math

import numpy as np
import pandas as pd

LOG = logging.getLogger("ksp.analytics")

EARTH_KM = 6371.0088


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return EARTH_KM * 2 * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------- 3. LINK PREDICTION
def build_link_prediction(graph, suspects_df, top_n=12):
    """Predict emerging criminal alliances: suspects who do not yet share a case but sit
    close in the co-offender network. Scored with the Adamic-Adar index (common
    associates, weighted so rare shared associates count for more)."""
    try:
        import networkx as nx

        # Build an undirected suspect co-offender graph: edge = shared case or known associate.
        G = nx.Graph()
        for n, data in graph.nodes(data=True):
            if data.get("type") == "Suspect":
                G.add_node(n)
        # co-offenders via shared cases
        for n, data in graph.nodes(data=True):
            if data.get("type") == "Case":
                susp = [nb for nb in graph.neighbors(n)
                        if graph.nodes[nb].get("type") == "Suspect"]
                for i in range(len(susp)):
                    for j in range(i + 1, len(susp)):
                        G.add_edge(susp[i], susp[j])
        # known-associate edges
        for u, v, ed in graph.edges(data=True):
            if ed.get("relationship") == "Known Associate" and G.has_node(u) and G.has_node(v):
                G.add_edge(u, v)

        if G.number_of_edges() == 0:
            return []

        # candidate non-edges = friend-of-friend pairs (bounded by degree)
        cand = set()
        for u in G.nodes():
            nbrs = set(G[u])
            for w in nbrs:
                for x in G[w]:
                    if x != u and not G.has_edge(u, x):
                        cand.add(frozenset((u, x)))
        if not cand:
            return []

        ebunch = [tuple(c) for c in cand]
        scored = sorted(nx.adamic_adar_index(G, ebunch), key=lambda t: t[2], reverse=True)

        name_map = {}
        if suspects_df is not None and not suspects_df.empty:
            name_map = dict(zip(suspects_df["suspect_id"], suspects_df["name"]))
        gang_map = {}
        risk_map = {}
        if suspects_df is not None and "gang_affiliation" in suspects_df.columns:
            gang_map = dict(zip(suspects_df["suspect_id"], suspects_df["gang_affiliation"]))
        if suspects_df is not None and "risk_level" in suspects_df.columns:
            risk_map = dict(zip(suspects_df["suspect_id"], suspects_df["risk_level"]))

        out = []
        for u, v, score in scored[:top_n]:
            shared = len(set(G[u]) & set(G[v]))
            out.append({
                "a": u, "a_name": name_map.get(u, u),
                "b": v, "b_name": name_map.get(v, v),
                "score": round(float(score), 3),
                "common_associates": int(shared),
                "same_gang": bool(gang_map.get(u) and gang_map.get(u) == gang_map.get(v)),
                "gang": gang_map.get(u) if gang_map.get(u) == gang_map.get(v) else None,
                "risk": risk_map.get(u) or risk_map.get(v),
            })
        return out
    except Exception as e:
        LOG.warning(f"Link prediction failed: {e}")
        return []


# ---------------------------------------------------------------- 5. RECIDIVISM RISK
_PROPERTY = {"Vehicle Theft", "Chain Snatching", "House Break-in (Night)"}
_CYBER = "Cyber Fraud (Jamtara style)"


def _pearson(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None
    r = float(np.corrcoef(x, y)[0, 1])
    return round(r, 2) if math.isfinite(r) else None


def _strength(r):
    a = abs(r)
    band = "strong" if a >= 0.6 else "moderate" if a >= 0.35 else "weak"
    return f"{band} {'positive' if r >= 0 else 'negative'}"


def build_socioeconomic_correlation(firs_df, socio_df):
    """Correlate district crime with socio-economic indicators (Challenge-02: socio-economic
    crime correlation). Returns per-district merged metrics, ranked Pearson correlations and
    plain-language findings."""
    try:
        if socio_df is None or socio_df.empty or firs_df is None:
            return {}

        # Per-district crime metrics from the live FIRs.
        g = firs_df.groupby("district")
        rows = []
        for d, sub in g:
            total = len(sub)
            cyber = int((sub["crime_type"] == _CYBER).sum())
            prop = int(sub["crime_type"].isin(_PROPERTY).sum())
            rows.append({
                "district": d,
                "total_crimes": total,
                "cyber_share": round(100.0 * cyber / total, 1) if total else 0.0,
                "property_share": round(100.0 * prop / total, 1) if total else 0.0,
                "avg_severity": round(float(sub["severity_score"].mean()), 1) if total else 0.0,
            })
        crime = pd.DataFrame(rows)
        m = socio_df.merge(crime, on="district", how="inner")
        if m.empty:
            return {}
        # crimes per 100k population (population_lakh × 100k people).
        m["crime_rate_per_100k"] = (m["total_crimes"] / (m["population_lakh"] * 100000.0) * 100000.0).round(1)

        districts = m.to_dict("records")
        for r in districts:
            for k, v in list(r.items()):
                if isinstance(v, (np.integer,)):
                    r[k] = int(v)
                elif isinstance(v, (np.floating,)):
                    r[k] = float(v)

        # Curated factor ↔ metric pairs that tell a policing story.
        pairs = [
            ("Unemployment", "unemployment", "Property-crime share", "property_share"),
            ("Unemployment", "unemployment", "Crime rate / 100k", "crime_rate_per_100k"),
            ("Urbanisation", "urbanization", "Cyber-fraud share", "cyber_share"),
            ("Literacy", "literacy", "Cyber-fraud share", "cyber_share"),
            ("Per-capita income", "per_capita_income_k", "Cyber-fraud share", "cyber_share"),
            ("Per-capita income", "per_capita_income_k", "Crime rate / 100k", "crime_rate_per_100k"),
            ("Police / 100k", "police_per_100k", "Crime rate / 100k", "crime_rate_per_100k"),
            ("Slum population", "slum_pct", "Property-crime share", "property_share"),
            ("Youth share", "youth_pct", "Crime rate / 100k", "crime_rate_per_100k"),
            ("Literacy", "literacy", "Avg severity", "avg_severity"),
        ]
        cors = []
        for fname, fcol, mname, mcol in pairs:
            if fcol in m and mcol in m:
                r = _pearson(m[fcol], m[mcol])
                if r is not None:
                    cors.append({"factor": fname, "metric": mname, "r": r, "direction": _strength(r)})
        cors.sort(key=lambda c: abs(c["r"]), reverse=True)

        insights = []
        for c in cors[:4]:
            verb = "rises with" if c["r"] >= 0 else "falls as"
            insights.append(f"{c['metric']} {verb} {c['factor'].lower()} "
                            f"({'+' if c['r'] >= 0 else ''}{c['r']}, {c['direction']}).")
        # A concrete drilldown nugget: highest crime-rate district.
        top = max(districts, key=lambda r: r["crime_rate_per_100k"])
        insights.append(f"Highest per-capita crime: {top['district']} at {top['crime_rate_per_100k']} "
                        f"per 100k (unemployment {top['unemployment']}%, literacy {top['literacy']}%).")

        return {"districts": districts, "correlations": cors, "insights": insights}
    except Exception as e:
        LOG.warning(f"Socio-economic correlation failed: {e}")
        return {}


_RISK_WEIGHT = {"Critical": 1.0, "High": 0.75, "Medium": 0.45, "Low": 0.2}


REPEAT_COOFFENCE_MIN = 2   # shared cases required before two people count as a crew


def detect_crime_communities(graph, suspects_df):
    """
    Applies Louvain community detection on the co-offender graph
    to automatically identify hidden organized crime cells/syndicates.
    """
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities

        # Project onto offenders before clustering. The raw graph is bipartite —
        # case nodes joined to suspect nodes — so running Louvain directly on it
        # returns communities made mostly of *cases*, which is why this used to
        # report hundreds of "syndicates" from a few hundred offenders. A syndicate
        # is a set of people, so the projection is the co-offender graph: an edge
        # between two suspects means they were named in the same case.
        G = nx.Graph()
        suspect_ids = set()
        if suspects_df is not None and not suspects_df.empty:
            suspect_ids = set(suspects_df["suspect_id"].astype(str))

        def _is_suspect(n):
            return (graph.nodes[n].get("type") == "Suspect") or (str(n) in suspect_ids)

        for node in graph.nodes():
            if graph.nodes[node].get("type") != "Case":
                continue
            members = [nb for nb in graph.neighbors(node) if _is_suspect(nb)]
            for i in range(len(members)):
                G.add_node(members[i])
                for j in range(i + 1, len(members)):
                    if G.has_edge(members[i], members[j]):
                        G[members[i]][members[j]]["weight"] += 1
                    else:
                        G.add_edge(members[i], members[j], weight=1)

        # Explicit known-associate links, where the registry records them.
        for u, v, ed in graph.edges(data=True):
            if ed.get("relationship") == "Known Associate" and _is_suspect(u) and _is_suspect(v):
                if not G.has_edge(u, v):
                    G.add_edge(u, v, weight=1)

        if G.number_of_nodes() == 0:
            return []
            
        # A syndicate is a set of people who offend together *repeatedly*. Two
        # strangers named in one case are co-accused, not a crew — and keeping those
        # single-case edges merges the whole registry into one giant component.
        core = nx.Graph()
        core.add_nodes_from(G.nodes())
        for u, v, ed in G.edges(data=True):
            if ed.get("weight", 1) >= REPEAT_COOFFENCE_MIN:
                core.add_edge(u, v, weight=ed["weight"])
        core.remove_nodes_from([n for n, d in core.degree() if d == 0])
        if core.number_of_nodes() == 0:
            return []

        try:
            communities = list(louvain_communities(core, weight="weight", random_state=42))
        except Exception:
            from networkx.algorithms.community import label_propagation_communities
            communities = list(label_propagation_communities(core))
            
        name_map = {}
        risk_map = {}
        gang_map = {}
        if suspects_df is not None and not suspects_df.empty:
            name_map = dict(zip(suspects_df["suspect_id"], suspects_df["name"]))
            risk_map = dict(zip(suspects_df["suspect_id"], suspects_df["risk_level"]))
            gang_map = dict(zip(suspects_df["suspect_id"], suspects_df["gang_affiliation"]))
            
        out = []
        for idx, comm in enumerate(communities):
            if len(comm) < 3:
                continue
                
            members = []
            total_risk_val = 0
            risk_weights = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
            
            for m in comm:
                r_lvl = risk_map.get(m, "Low")
                total_risk_val += risk_weights.get(r_lvl, 1)
                members.append({
                    "id": m,
                    "name": name_map.get(m, m),
                    "risk": r_lvl,
                    "legacy_gang": gang_map.get(m, "None")
                })
                
            avg_risk = total_risk_val / len(comm)
            ring_risk = "Critical" if avg_risk >= 3.2 else "High" if avg_risk >= 2.5 else "Medium" if avg_risk >= 1.5 else "Low"
            
            out.append({
                "syndicate_id": f"SYN-RING-{100 + idx}",
                "size": len(comm),
                "risk_level": ring_risk,
                "members": members,
                "legacy_gangs_represented": list(set(m["legacy_gang"] for m in members if m["legacy_gang"] != "None"))
            })
            
        out = sorted(out, key=lambda x: (x["size"], x["risk_level"]), reverse=True)
        return out
    except Exception as e:
        LOG.warning(f"Community detection failed: {e}")
        return []


def _suspect_history_index(firs_df, df_acc, df_crim, data_cache):
    """Everything `recommend_suspects_for_case` needs about the past, built once.

    None of this depends on which case is being viewed, yet it was rebuilt on every
    request: a full merge plus a grouping that materialised one DataFrame per suspect.
    On a 512 MB container that was ~19 MB per request and, under concurrency, an OOM
    kill mid-response.

    Stored as plain lists and arrays rather than DataFrames — the per-suspect column
    access was the bulk of what remained after the scans were removed.
    """
    import numpy as np
    import pandas as pd

    # Identity and length together: a rebuilt frame gets a new id, and an in-place
    # mutation that changed the row count is caught too. `/sync` rebuilds with
    # force=True without clearing data_cache, so a plain "is it present" check would
    # serve a stale index after a resync.
    key = (id(df_acc), len(df_acc), id(firs_df), len(firs_df), id(df_crim), len(df_crim))
    cached = data_cache.get('_suspect_history_index')
    if cached is not None and cached.get('key') == key:
        return cached

    merged = df_acc.merge(firs_df, left_on='CaseMasterID',
                          right_on='CaseMasterID', how='inner')

    past = {}
    for pid, grp in merged.groupby('PersonID', sort=False):
        lats = pd.to_numeric(grp['latitude'], errors='coerce').to_numpy(dtype=float)
        lngs = pd.to_numeric(grp['longitude'], errors='coerce').to_numpy(dtype=float)
        past[pid] = {
            'types': grp['crime_type'].tolist(),
            # Pre-filtered exactly as the caller did, so membership tests are
            # unchanged: falsy values and the literal 'None' were excluded.
            'weapons': {w for w in grp['weapon_used'].tolist() if w and w != 'None'},
            'vehicles': {v for v in grp['vehicle_used'].tolist() if v and v != 'None'},
            'targets': {t for t in grp['target_type'].tolist() if t and t != 'Unknown'},
            'districts': set(grp['district'].tolist()),
            'lats': lats,
            'lngs': lngs,
        }

    accused_by_case = {}
    for cmid, grp in df_acc.groupby('CaseMasterID', sort=False):
        accused_by_case[cmid] = set(grp['PersonID'])

    # First row per id, matching the previous `.iloc[0]` on a filtered frame.
    criminals = {}
    for cid, grp in df_crim.groupby('criminal_id', sort=False):
        row = grp.iloc[0]
        criminals[cid] = {
            'rowdy_sheeter': bool(row.get('rowdy_sheeter')),
            'active_warrant': bool(row.get('active_warrant')),
            'history_sheet': bool(row.get('history_sheet')),
            'past_arrests': int(row.get('past_arrests', 0) or 0),
        }

    index = {'key': key, 'past': past, 'accused_by_case': accused_by_case,
             'criminals': criminals}
    data_cache['_suspect_history_index'] = index
    return index


def recommend_suspects_for_case(case_id, firs_df, suspects_df, accused_df, data_cache, k=5):
    """
    Suggests the top k most likely suspects for a specific case based on:
    - MO overlap (crime type, weapon, vehicle, target)
    - Spatiotemporal proximity to suspect's past crime history
    - Risk & Recidivism profile of the suspect (rowdy sheeter, warrants, etc.)
    """
    try:
        import pandas as pd
        import numpy as np
        
        case_row = firs_df[firs_df['case_id'] == str(case_id)]
        if case_row.empty:
            return []
            
        case_info = case_row.iloc[0].to_dict()
        c_type = case_info.get('crime_type')
        c_weapon = case_info.get('weapon_used', 'None')
        c_vehicle = case_info.get('vehicle_used', 'None')
        c_target = case_info.get('target_type', 'Unknown')
        c_lat = float(case_info.get('latitude', 0.0))
        c_lng = float(case_info.get('longitude', 0.0))
        c_dist = case_info.get('district', '')

        df_acc = data_cache.get('Accused')
        df_crim = data_cache.get('criminals')
        
        if df_acc is None or df_crim is None or df_acc.empty or df_crim.empty:
            return []
            
        index = _suspect_history_index(firs_df, df_acc, df_crim, data_cache)
        past_by_person = index['past']
        already_accused = index['accused_by_case'].get(case_info['CaseMasterID'], frozenset())
        criminal_by_id = index['criminals']
        
        recommendations = []
        for _, s_row in suspects_df.iterrows():
            s_id = s_row['suspect_id']
            if s_id in already_accused:
                continue
                
            s_past = past_by_person.get(s_id)
            
            mo_score = 0.0
            loc_score = 0.0
            risk_score = 0.0
            justifications = []
            
            # 1. MO Similarities (Max 40)
            if s_past is not None:
                type_matches = s_past['types'].count(c_type)
                if type_matches > 0:
                    mo_score += 20.0
                    justifications.append(f"Matching crime type history ({type_matches} times)")

                if c_weapon and c_weapon != 'None' and c_weapon in s_past['weapons']:
                    mo_score += 10.0
                    justifications.append(f"Uses matching weapon ({c_weapon})")

                if c_vehicle and c_vehicle != 'None' and c_vehicle in s_past['vehicles']:
                    mo_score += 5.0
                    justifications.append(f"Uses matching vehicle ({c_vehicle})")

                if c_target and c_target != 'Unknown' and c_target in s_past['targets']:
                    mo_score += 5.0
                    justifications.append(f"Targets same profile ({c_target})")
            else:
                gang = str(s_row.get('gang_affiliation', '')).lower()
                if c_type == 'Cyber Fraud (Jamtara style)' and 'cyber' in gang:
                    mo_score += 20.0
                    justifications.append("Affiliated with Cyber Phishing syndicate")
                elif c_type == 'Narcotics' and 'narcotics' in gang:
                    mo_score += 20.0
                    justifications.append("Affiliated with drug syndicate")
                    
            # 2. Spatiotemporal/Geographic Proximity (Max 35)
            if s_past is not None:
                if c_dist in s_past['districts']:
                    loc_score += 15.0
                    justifications.append(f"Operated in {c_dist} district previously")

                # Vectorised over this suspect's past coordinates. The scalar form
                # was called 28,346 times for a single dossier, and each call ran
                # np.radians over a four-element list — numpy scalar overhead, paid
                # per past case. Same formula, same elementwise arithmetic, so the
                # minimum is unchanged.
                min_dist = _haversine_min(c_lat, c_lng, s_past['lats'], s_past['lngs'])
                if min_dist < 5.0:
                    dist_contrib = max(5.0, 20.0 - min_dist * 3.0)
                    loc_score += dist_contrib
                    justifications.append(f"Crime scene within {min_dist:.1f}km of past activity")
                elif min_dist < 15.0:
                    loc_score += 10.0
                    justifications.append(f"Crime scene within {min_dist:.1f}km of past activity")

            # 3. Offender Risk Profile (Max 25)
            c_data = criminal_by_id.get(s_id)
            if c_data is not None:
                if c_data['rowdy_sheeter']:
                    risk_score += 8.0
                    justifications.append("Flagged Rowdy Sheeter")
                if c_data['active_warrant']:
                    risk_score += 8.0
                    justifications.append("Active arrest warrant outstanding")
                if c_data['history_sheet']:
                    risk_score += 4.0
                    justifications.append("Opened history sheet records")

                past_arrs = c_data['past_arrests']
                if past_arrs > 0:
                    risk_score += min(5.0, past_arrs * 1.5)
                    justifications.append(f"Has {past_arrs} past arrests")

            total_score = int(round(mo_score + loc_score + risk_score))
            if total_score > 10:
                recommendations.append({
                    "suspect_id": s_id,
                    "name": s_row['name'],
                    "risk_level": s_row.get('risk_level', 'Medium'),
                    "match_score": min(98, max(12, total_score)),
                    "evidence_components": {
                        "modus_operandi": round(mo_score, 1),
                        "spatial_proximity": round(loc_score, 1),
                        "offender_profile": round(risk_score, 1),
                    },
                    "justifications": justifications[:3]
                })

        # Ties are broken by the strength of the MO match, then by suspect_id.
        # The previous version added `hash(s_id) % 5` here to make the percentages
        # look distinct; Python randomises str hashing per process, so a suspect's
        # match score changed on every server restart. Ranking must be reproducible
        # when it is the basis for naming someone a suspect.
        recommendations = sorted(
            recommendations,
            key=lambda x: (x['match_score'], x['evidence_components']['modus_operandi'],
                           x['suspect_id']),
            reverse=True)
        return recommendations[:k]
    except Exception as e:
        LOG.warning(f"Suspect recommendation failed: {e}")
        return []


def _haversine_min(lat, lon, lats, lngs):
    """Smallest great-circle distance from one point to an array of points.

    Returns the same 999.0 sentinel the scalar loop used when there are no past
    coordinates, so the callers' `< 5.0` / `< 15.0` bands behave identically.
    """
    import numpy as np

    lats = np.asarray(lats, dtype=float)
    lngs = np.asarray(lngs, dtype=float)
    if lats.size == 0:
        return 999.0
    R = 6371.0
    lat1 = np.radians(lat)
    lon1 = np.radians(lon)
    lat2 = np.radians(lats)
    lon2 = np.radians(lngs)
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    d = R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    # NaN coordinates propagated to NaN in the scalar path too, and min() over a
    # list containing NaN is order-dependent; nanmin makes the choice explicit.
    if np.all(np.isnan(d)):
        return 999.0
    return float(np.nanmin(d))


def haversine_distance_scalar(lat1, lon1, lat2, lon2):
    import numpy as np
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dLat = lat2 - lat1
    dLon = lon2 - lon1
    a = np.sin(dLat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dLon / 2) ** 2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return R * c


def get_suspect_crime_correlation(suspect_id, case_rows, firs_df, data_cache):
    """
    Computes a detailed criminological, temporal and socio-economic correlation report
    for a single suspect based on their history vs. overall system patterns.
    """
    try:
        import pandas as pd
        import numpy as np
        
        if case_rows.empty or firs_df.empty:
            return {}
            
        # 1. Temporal Correlation
        # Compare suspect's hour band distribution with the system-wide distribution
        suspect_tod = case_rows['time_of_day'].value_counts()
        system_tod = firs_df['time_of_day'].value_counts()
        
        all_tods = ["Morning (7AM-10AM)", "Afternoon (1PM-4PM)", "Evening (5PM-8PM)", "Late Night (1AM-4AM)"]
        sus_vec = np.array([suspect_tod.get(tod, 0) for tod in all_tods], dtype=float)
        sys_vec = np.array([system_tod.get(tod, 0) for tod in all_tods], dtype=float)
        
        # Normalize to percentages
        if sus_vec.sum() > 0:
            sus_vec /= sus_vec.sum()
        if sys_vec.sum() > 0:
            sys_vec /= sys_vec.sum()
            
        # Cosine similarity for temporal profile
        dot = np.dot(sus_vec, sys_vec)
        norm_sus = np.linalg.norm(sus_vec)
        norm_sys = np.linalg.norm(sys_vec)
        temporal_similarity = float(dot / (norm_sus * norm_sys)) if norm_sus > 0 and norm_sys > 0 else 1.0
        
        # 2. Socio-economic Correlation
        # Overlay the suspect's active districts with socio-economic data
        socio_df = data_cache.get('socio')
        sus_districts = case_rows['district'].value_counts().to_dict()
        
        socio_profile = {}
        if socio_df is not None and not socio_df.empty:
            active_socios = socio_df[socio_df['district'].isin(sus_districts.keys())]
            if not active_socios.empty:
                # Weighted averages by frequency of suspect crimes in each district
                total_sus_crimes = sum(sus_districts.values())
                weights = {d: count / total_sus_crimes for d, count in sus_districts.items()}
                
                # State-wide averages
                state_unemp = float(socio_df['unemployment'].mean())
                state_urban = float(socio_df['urbanization'].mean())
                state_lit = float(socio_df['literacy'].mean())
                state_income = float(socio_df['per_capita_income_k'].mean())
                
                # Suspect weighted metrics
                sus_unemp = float(sum(active_socios['unemployment'] * active_socios['district'].map(weights)))
                sus_urban = float(sum(active_socios['urbanization'] * active_socios['district'].map(weights)))
                sus_lit = float(sum(active_socios['literacy'] * active_socios['district'].map(weights)))
                sus_income = float(sum(active_socios['per_capita_income_k'] * active_socios['district'].map(weights)))
                
                socio_profile = {
                    "suspect_weighted": {
                        "unemployment": round(sus_unemp, 1),
                        "urbanization": round(sus_urban, 1),
                        "literacy": round(sus_lit, 1),
                        "per_capita_income_k": round(sus_income, 1)
                    },
                    "state_average": {
                        "unemployment": round(state_unemp, 1),
                        "urbanization": round(state_urban, 1),
                        "literacy": round(state_lit, 1),
                        "per_capita_income_k": round(state_income, 1)
                    },
                    "insights": [
                        f"Unemployment in active zones is {round(sus_unemp - state_unemp, 1):+}% vs. state average.",
                        f"Urbanization is {round(sus_urban - state_urban, 1):+}% vs. state average.",
                        f"Operates in districts with an average per-capita income of ₹{round(sus_income, 1)}k (state average: ₹{round(state_income, 1)}k)."
                    ]
                }
        
        # 3. Behavioral deviance score
        # deviation in severity vs average system-wide severity
        sus_avg_sev = float(case_rows['severity_score'].mean())
        sys_avg_sev = float(firs_df['severity_score'].mean())
        sys_std_sev = float(firs_df['severity_score'].std()) if len(firs_df) > 1 else 10.0
        severity_z_score = float((sus_avg_sev - sys_avg_sev) / sys_std_sev) if sys_std_sev > 0 else 0.0
        
        # 4. Crime Specialization
        crime_counts = case_rows['crime_type'].value_counts()
        primary_crime = crime_counts.index[0]
        primary_crime_pct = float(crime_counts.iloc[0] / len(case_rows))
        
        specialization_level = "High Specialist" if primary_crime_pct > 0.7 else "Generalist / Versatile" if primary_crime_pct < 0.4 else "Moderate Specialist"
        
        # 5. Co-offender Connections
        # Find people who co-offended with this suspect in the accused table
        df_acc = data_cache.get('Accused')
        df_suspects = data_cache.get('suspects')
        co_offenders = []
        if df_acc is not None and not df_acc.empty and df_suspects is not None:
            my_cases = df_acc[df_acc['PersonID'] == suspect_id]['CaseMasterID'].tolist()
            other_accused = df_acc[(df_acc['CaseMasterID'].isin(my_cases)) & (df_acc['PersonID'] != suspect_id)]
            if not other_accused.empty:
                co_ids = other_accused['PersonID'].unique().tolist()
                co_sus = df_suspects[df_suspects['suspect_id'].isin(co_ids)]
                for _, co_row in co_sus.iterrows():
                    co_offenders.append({
                        "id": co_row['suspect_id'],
                        "name": co_row['name'],
                        "risk_level": co_row.get('risk_level', 'Medium'),
                        "gang_affiliation": co_row.get('gang_affiliation', 'None')
                    })
        
        return {
            "temporal_similarity": round(temporal_similarity, 3),
            "temporal_deviance_pct": round(100.0 * (1.0 - max(0.0, temporal_similarity)), 1),
            "socio_profile": socio_profile,
            "severity_deviance_z": round(severity_z_score, 2),
            "specialization": {
                "primary_crime": primary_crime,
                "percentage": round(100.0 * primary_crime_pct, 1),
                "level": specialization_level
            },
            "co_offenders": co_offenders[:5]
        }
    except Exception as e:
        LOG.warning(f"Suspect correlation calculation failed: {e}")
        return {}


# ---------------------------------------------------------------- case lifecycle
def build_case_lifecycle(bundle, cohort_days=730):
    """FIR -> arrest -> chargesheet -> verdict funnel, measured on a closed cohort.

    Pendency is the metric SCRB is actually judged on by the state, and a funnel is
    how you see *where* cases stop moving rather than only that they do.

    **Why a cohort and not the whole corpus.** A case registered last month has not
    failed to reach chargesheet; it simply has not had time. Running the funnel
    over every case ever registered mixes those in as losses and understates every
    conversion rate — the same right-censoring problem `ml/tasks/pendency.py`
    exists to handle, in a descriptive statistic rather than a model. The headline
    funnel is therefore computed on cases registered at least `cohort_days` before
    the extract date, which have had a realistic opportunity to progress. The
    all-cases figures are returned alongside, clearly labelled, so the difference
    is visible instead of hidden.

    Stage timings are medians, not means: investigation durations are heavily
    right-skewed and a mean is dragged by a handful of very old files.
    """
    import numpy as np
    import pandas as pd

    cases = bundle.get("cases")
    if cases is None or cases.empty:
        return {}

    df = cases.dropna(subset=["ts"]).copy()
    df["registered_ts"] = df["registered_ts"].fillna(df["ts"])
    extract_date = df["registered_ts"].max()

    arrests = bundle.get("arrests")
    chargesheets = bundle.get("chargesheets")
    courts = bundle.get("courts")

    first_arrest = (arrests.dropna(subset=["arrest_date"]).groupby("case_key")["arrest_date"].min()
                    if arrests is not None and not arrests.empty else pd.Series(dtype="datetime64[ns]"))
    first_cs = (chargesheets.dropna(subset=["chargesheet_date"]).groupby("case_key")["chargesheet_date"].min()
                if chargesheets is not None and not chargesheets.empty else pd.Series(dtype="datetime64[ns]"))

    df["arrest_date"] = df["case_key"].map(first_arrest)
    df["chargesheet_date"] = df["case_key"].map(first_cs)

    verdict = {}
    if courts is not None and not courts.empty and "conviction_status" in courts.columns:
        verdict = courts.set_index("case_key")["conviction_status"].to_dict()
    df["verdict"] = df["case_key"].map(verdict)

    def _funnel(frame, label):
        n = len(frame)
        if n == 0:
            return None
        arrested = int(frame["arrest_date"].notna().sum())
        charged = int(frame["chargesheet_date"].notna().sum())
        concluded = int(frame["verdict"].isin(["Convicted", "Acquitted"]).sum())
        convicted = int((frame["verdict"] == "Convicted").sum())

        def _median_gap(a, b):
            gap = (frame[b] - frame[a]).dt.days
            gap = gap[gap.notna() & (gap >= 0)]
            return int(gap.median()) if len(gap) else None

        stages = [
            {"stage": "FIR registered", "count": n, "share": 1.0, "median_days_from_prev": 0},
            {"stage": "Arrest made", "count": arrested,
             "share": round(arrested / n, 4),
             "median_days_from_prev": _median_gap("registered_ts", "arrest_date")},
            {"stage": "Chargesheet filed", "count": charged,
             "share": round(charged / n, 4),
             "median_days_from_prev": _median_gap("arrest_date", "chargesheet_date")},
            {"stage": "Trial concluded", "count": concluded,
             "share": round(concluded / n, 4),
             "median_days_from_prev": None},
            {"stage": "Conviction", "count": convicted,
             "share": round(convicted / n, 4),
             "median_days_from_prev": None},
        ]
        # Step-to-step conversion is what identifies the leak; the share column
        # alone hides whether a stage is the bottleneck or merely downstream of one.
        prev = n
        for s in stages:
            s["step_conversion"] = round(s["count"] / prev, 4) if prev else None
            prev = s["count"] or prev
        return {"label": label, "n": n, "stages": stages,
                "conviction_rate_of_concluded": round(convicted / concluded, 4) if concluded else None}

    cohort = df[df["registered_ts"] <= extract_date - pd.Timedelta(days=cohort_days)]
    closed = _funnel(cohort, f"registered >= {cohort_days} days before extract")
    everything = _funnel(df, "all registered cases")

    # Per-district, on the cohort only — comparing districts on a mixture of
    # mature and brand-new cases would rank them by how recently they were busy.
    by_district = []
    for name, g in cohort.groupby("district"):
        if len(g) < 30:
            continue
        charged = int(g["chargesheet_date"].notna().sum())
        concluded = int(g["verdict"].isin(["Convicted", "Acquitted"]).sum())
        convicted = int((g["verdict"] == "Convicted").sum())
        gap = (g["chargesheet_date"] - g["registered_ts"]).dt.days
        gap = gap[gap.notna() & (gap >= 0)]
        by_district.append({
            "district": name,
            "cases": int(len(g)),
            "chargesheet_rate": round(charged / len(g), 4),
            "conclusion_rate": round(concluded / len(g), 4),
            "conviction_rate": round(convicted / concluded, 4) if concluded else None,
            "median_days_to_chargesheet": int(gap.median()) if len(gap) else None,
        })
    by_district.sort(key=lambda x: x["chargesheet_rate"])

    return {
        "cohort": closed,
        "all_cases": everything,
        "by_district": by_district,
        "extract_date": str(extract_date.date()) if pd.notna(extract_date) else None,
        "cohort_days": cohort_days,
        "note": ("The headline funnel uses a closed cohort — cases registered at least "
                 f"{cohort_days} days before the extract — so that cases still legitimately "
                 "in progress are not counted as failures. All-case figures are shown "
                 "alongside and will always look worse for exactly that reason."),
    }


# ---------------------------------------------------------------- operational index
ORI_WEIGHTS = {
    "crime_rate": 0.25,        # cases per lakh population
    "pendency": 0.25,          # share of open cases past 90 days
    "chargesheet_gap": 0.20,   # failure to convert FIRs into chargesheets
    "severity": 0.15,          # mean severity of recent incidents
    "trend": 0.15,             # recent volume vs the preceding baseline
}


def build_operational_index(bundle, weights=None):
    """District Operational Risk Index, 0-100.

    **This is an index, not a model, and the distinction is published on screen.**
    It is a weighted blend of five measured quantities. The weights are a *policy
    choice* about what a command centre considers serious — not an estimate of
    anything, and not fitted to data. Change them and the ranking changes. Every
    component is returned with its raw value, its percentile and its exact
    contribution in points, so any number on the dashboard can be taken apart.

    A single opaque 0-100 "risk score" with no visible arithmetic is the easiest
    thing in this domain to produce and the hardest to defend, which is why the
    breakdown ships with the score rather than behind it.

    Components are **percentile-ranked across districts** before weighting.
    Absolute units are not comparable — cases per lakh and mean severity live on
    different scales — and ranking also stops one outlier district dominating the
    way a min-max normalisation would allow.

    Crime rate is per *capita* using real 2011 Census population, which is only
    meaningful because district case volume is now population-weighted rather
    than uniform.
    """
    import numpy as np
    import pandas as pd

    import karnataka as KA

    cases = bundle.get("cases")
    if cases is None or cases.empty:
        return {}

    df = cases.dropna(subset=["ts"]).copy()
    df["registered_ts"] = df["registered_ts"].fillna(df["ts"])
    extract = df["registered_ts"].max()

    chargesheets = bundle.get("chargesheets")
    first_cs = (chargesheets.dropna(subset=["chargesheet_date"])
                .groupby("case_key")["chargesheet_date"].min()
                if chargesheets is not None and not chargesheets.empty
                else pd.Series(dtype="datetime64[ns]"))
    df["chargesheet_date"] = df["case_key"].map(first_cs)

    recent_cut = extract - pd.Timedelta(days=90)
    prior_cut = extract - pd.Timedelta(days=455)   # the same 90-day window a year earlier
    prior_end = extract - pd.Timedelta(days=365)

    rows = []
    for name, g in df.groupby("district"):
        pop = KA.population(name)
        recent = g[g["registered_ts"] > recent_cut]
        prior = g[(g["registered_ts"] > prior_cut) & (g["registered_ts"] <= prior_end)]

        open_cases = g[g["chargesheet_date"].isna()]
        days_open = (extract - open_cases["registered_ts"]).dt.days
        past_90 = float((days_open > 90).mean()) if len(days_open) else 0.0

        charged = float(g["chargesheet_date"].notna().mean())
        sev = float(recent["severity_score"].mean()) if len(recent) else float(g["severity_score"].mean())
        trend = (len(recent) / len(prior)) if len(prior) >= 5 else 1.0

        rows.append({
            "district": name,
            "population_lakh": pop,
            "cases": int(len(g)),
            "raw": {
                "crime_rate": round(len(g) / pop, 2),          # cases per lakh
                "pendency": round(past_90, 4),
                "chargesheet_gap": round(1.0 - charged, 4),
                "severity": round(sev, 1),
                "trend": round(trend, 3),
            },
        })

    if len(rows) < 2:
        return {}

    w = dict(ORI_WEIGHTS)
    if weights:
        w.update({k: v for k, v in weights.items() if k in w})
    total_w = sum(w.values()) or 1.0

    # Percentile-rank each component across districts (0 = lowest risk).
    for key in w:
        vals = np.array([r["raw"][key] for r in rows], dtype=float)
        order = vals.argsort().argsort()
        pct = order / max(1, len(vals) - 1)
        for r, p in zip(rows, pct):
            r.setdefault("percentile", {})[key] = round(float(p), 4)

    for r in rows:
        contrib = {k: round(100.0 * r["percentile"][k] * w[k] / total_w, 2) for k in w}
        r["contribution"] = contrib
        r["index"] = round(sum(contrib.values()), 1)
        r["band"] = ("Critical" if r["index"] >= 75 else
                     "High" if r["index"] >= 55 else
                     "Moderate" if r["index"] >= 35 else "Watch")

    rows.sort(key=lambda x: -x["index"])
    return {
        "districts": rows,
        "weights": w,
        "components": {
            "crime_rate": "registered cases per lakh population (2011 Census denominator)",
            "pendency": "share of still-open cases already past 90 days",
            "chargesheet_gap": "share of cases that never reached a chargesheet",
            "severity": "mean severity score of incidents in the last 90 days",
            "trend": "last-90-day volume divided by the same 90 days one year earlier",
        },
        "method": ("Each component is percentile-ranked across the 31 districts, then "
                   "weighted and summed to 0-100. Percentile rather than min-max so one "
                   "outlier district cannot dominate, and because the components are on "
                   "different units."),
        "caveat": ("An index, not a model. The weights encode a policy judgement about "
                   "what counts as serious; they are not estimated from data and nothing "
                   "validates them. Every component value, percentile and point "
                   "contribution is published so the score can be taken apart. Read it as "
                   "a way to sort districts for attention — never as a measurement of "
                   "policing quality or of any officer."),
    }
