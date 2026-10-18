"""Link-analysis workbench over the knowledge graph.

`GET /network` in main.py answers one question — "show me the cases around this
person" — with fixed slices (first ten cases, first six neighbours of each), so a
hub returned an arbitrary subset and nothing said which. An investigator doing link
analysis needs more than that: find an entity by any identifier they hold, expand
outward under a budget they can see, ask how two entities are connected, and read a
profile without leaving the graph. This module is that workbench.

Everything here is read-only over the graph main.py builds. The graph is rebound on
every reload, so it is only ever read through the getter passed to `configure`; a
reference captured at import time would silently keep serving the previous corpus.

Limits, stated plainly
----------------------
* Expansion is bounded. A hop keeps at most a per-node share of the remaining
  budget, ranked by a stated priority (type, registry risk, warrant/rowdy flags,
  degree), so an officer with 250 cases cannot return 10,000 nodes. Every response
  says whether anything was left out and how much.
* Officers are de-prioritised and excluded from paths by default. An investigating
  officer is an administrative link: two strangers whose FIRs were handled by the
  same inspector are not associates, and with officers allowed nearly every pair of
  people in the state is four hops apart through one.
* Co-accused edges between people (`derived: true`) are computed from shared FIRs,
  not recorded in any register. They are drawn only when the shared FIRs themselves
  are not on screen, so the same evidence is never shown twice.
* "Predicted" links are Adamic-Adar scores over the co-offender projection — the
  method `analytics.build_link_prediction` uses for the global list. They are leads
  to check, not evidence of anything.
* Syndicates are the Louvain communities main.py already computes over repeat
  co-offending (two or more shared FIRs). Most people are in none; they are shown
  without a community rather than assigned one.
* The recidivism index is withheld unless the caller holds `watchlist_names` for a
  jurisdiction covering the person — the same rule `/recidivism` enforces. A graph
  side panel must not become the side door to the watchlist.
"""
import heapq
import json
import math
import re
import threading
import time
from collections import Counter
from urllib.parse import quote

import networkx as nx
from flask import Blueprint, Response, request

import audit
import auth

bp = Blueprint("network_intel", __name__)

_graph_getter = lambda: None   # noqa: E731 — replaced by configure()
_cache_getter = lambda: {}     # noqa: E731


def configure(graph_getter, cache_getter):
    """Bind the live graph and data cache. Both are callables, never values."""
    global _graph_getter, _cache_getter
    _graph_getter = graph_getter
    _cache_getter = cache_getter


# --------------------------------------------------------------------- constants
TYPES = ("Suspect", "Case", "Officer", "Vehicle", "CyberID")
_TYPE_ALIASES = {
    "suspect": "Suspect", "suspects": "Suspect", "person": "Suspect", "people": "Suspect",
    "case": "Case", "cases": "Case", "fir": "Case", "firs": "Case",
    "officer": "Officer", "officers": "Officer",
    "vehicle": "Vehicle", "vehicles": "Vehicle",
    "cyberid": "CyberID", "cyber": "CyberID", "identifier": "CyberID", "identifiers": "CyberID",
}

MAX_NODES_CEILING = 500        # what a browser canvas holds at 60fps with labels rationed
DEFAULT_SEEDED_NODES = 150
OVERVIEW_NODES = 350
MAX_SEEDS = 8
SEARCH_LIMIT = 20
PATH_K_MAX = 5
PATH_MAX_HOPS = 10
PATH_TIME_BUDGET_S = 2.5
PREDICTED_IN_VIEW = 15
HOP_SHARE = 0.55               # share of the remaining cap a non-final hop may use

# Expansion priority. Declared rather than buried so it can be argued with.
#
# Ranked by the EDGE that reaches a neighbour, not by its type alone. Ranking
# people above FIRs regardless of edge let registry associates crowd out a seed's
# own FIRs, so "Bawaria Gang" came back as 150 people and no FIR — the evidence
# that joins them was the first thing cut. A FIR record (charged, co-offender, an
# identifier or vehicle traced in it) is the strongest link; a registry associate
# is next; an investigating officer is administrative and comes last.
REL_PRIORITY = {
    "Charged": 30.0, "Co-Offender": 30.0, "Cyber Trace": 28.0, "Vehicle Involved": 27.0,
    "Co-accused": 27.0, "Known Associate": 22.0, "Investigating Officer": 8.0,
}
TYPE_PRIORITY = {"Suspect": 24.0, "CyberID": 26.0, "Vehicle": 24.0, "Case": 24.0, "Officer": 8.0}
RISK_WEIGHT = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
# Edge weight is strength of association evidence: one recorded link is 1, a derived
# co-accused edge is its number of shared FIRs, and an investigating-officer edge is
# 0.5 because it is not evidence of association at all.
RELATIONSHIP_WEIGHT = {"Investigating Officer": 0.5}

CYBER_KIND = {"PHONE": "Phone number", "UPI": "UPI handle", "ACCT": "Bank account"}

_STOPWORDS = {
    "show", "me", "the", "a", "an", "of", "for", "and", "to", "in", "on", "with",
    "network", "graph", "links", "link", "connections", "connection", "associates",
    "find", "open", "who", "is", "are", "all", "please",
}


# --------------------------------------------------------------------- helpers
def _json(payload, status=200):
    """A JSON response. Errors are returned as a (response, status) tuple because
    that is the only shape `audit.audited` reads a status from — a bare Response
    with a 404 inside would be logged as a successful read."""
    body = Response(json.dumps(_clean(payload)), mimetype="application/json")
    return (body, status) if status != 200 else body


def _clean(obj):
    """NaN/inf -> None and numpy scalars -> Python, recursively. JSON.parse rejects
    the bare NaN tokens json.dumps would otherwise emit."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            return _clean(obj.item())
        except Exception:
            return str(obj)
    return obj


def _s(v):
    """A clean string or None. Registry columns carry NaN for 'not recorded'."""
    if v is None:
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    out = str(v).strip()
    return out if out and out.lower() not in ("nan", "none", "null") else None


def _b(v):
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y")
    try:
        return bool(v) and not (isinstance(v, float) and not math.isfinite(v))
    except Exception:
        return False


def _int(v, default=None):
    try:
        out = int(float(v))
        return out
    except (TypeError, ValueError):
        return default


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _norm_plate(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _ok_preflight():
    return "", 204


def _short(text, n=24):
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


# --------------------------------------------------------------------- the index
class _Index:
    """Everything the endpoints look up, built once per published graph.

    Plain dicts keyed by node id, holding references to values the DataFrames
    already own. Rebuilt whenever the graph or a source frame is replaced — `/sync`
    rebinds all of them, and a stale index would answer about the previous corpus.
    """

    def __init__(self, G, cache):
        self.key = _index_key(G, cache)
        self.case = {}
        firs = cache.get("firs")
        if firs is not None and not firs.empty:
            cols = {c: (firs[c].tolist() if c in firs.columns else [None] * len(firs))
                    for c in ("case_id", "district", "crime_type", "police_station", "status",
                              "timestamp", "severity_score", "ipc_bns_sections", "CaseNo")}
            for i, cid in enumerate(cols["case_id"]):
                ts = _s(cols["timestamp"][i]) or ""
                self.case[str(cid)] = {
                    "district": _s(cols["district"][i]),
                    "crime_type": _s(cols["crime_type"][i]),
                    "station": _s(cols["police_station"][i]),
                    "status": _s(cols["status"][i]),
                    "timestamp": ts or None,
                    "year": _int(ts[:4]),
                    "severity": _int(cols["severity_score"][i]),
                    "sections": _s(cols["ipc_bns_sections"][i]),
                    "case_no": _s(cols["CaseNo"][i]),
                }

        self.person = {}
        crim = cache.get("criminals")
        if crim is not None and not crim.empty:
            for row in crim.to_dict("records"):
                sid = _s(row.get("criminal_id"))
                if not sid:
                    continue
                alias = _s(row.get("alias"))
                self.person[sid] = {
                    "name": _s(row.get("name")),
                    "alias": alias.strip("'\"") if alias else None,
                    "gang": _s(row.get("gang_affiliation")),
                    "risk": _s(row.get("risk_level")),
                    "photo": _s(row.get("photo_url")),
                    "past_fir_count": _int(row.get("past_fir_count")),
                    "past_arrests": _int(row.get("past_arrests")),
                    "rowdy": _b(row.get("rowdy_sheeter")),
                    "history_sheet": _b(row.get("history_sheet")),
                    "warrant": _b(row.get("active_warrant")),
                }

        self.officer = {}
        offs = cache.get("officers")
        if offs is not None and not offs.empty:
            for row in offs.to_dict("records"):
                oid = _s(row.get("officer_id"))
                if oid:
                    self.officer[oid] = {
                        "name": _s(row.get("name")), "rank": _s(row.get("rank")),
                        "station": _s(row.get("station")), "district": _s(row.get("district")),
                        "specialization": _s(row.get("specialization")),
                        "closure_rate": row.get("closure_rate"),
                    }

        self.vehicle = {}
        veh = cache.get("vehicles")
        if veh is not None and not veh.empty:
            for row in veh.to_dict("records"):
                plate = _s(row.get("vehicle_number"))
                if plate:
                    # Owner name and phone are deliberately not carried: the owner is
                    # the victim of a theft, and link analysis has no use for them.
                    self.vehicle[plate] = {
                        "make": _s(row.get("make_model")),
                        "recovery": _s(row.get("recovery_status")),
                        "theft_district": _s(row.get("theft_district")),
                        "theft_date": _s(row.get("theft_date")),
                    }

        self.cyber_case = {}
        cyb = cache.get("cybercrime")
        if cyb is not None and not cyb.empty:
            for row in cyb[["case_id", "fraud_amount", "victim_count"]].to_dict("records"):
                self.cyber_case[str(row["case_id"])] = (
                    float(row["fraud_amount"]) if _int(row["fraud_amount"]) is not None else 0.0,
                    _int(row["victim_count"], 0))

        # Type sets and the co-offender projection, from the graph itself.
        self.of_type = {t: set() for t in TYPES}
        for n, d in G.nodes(data=True):
            t = d.get("type")
            if t in self.of_type:
                self.of_type[t].add(n)

        # sid -> {sid2: [case ids they are both accused in]}
        self.cooff = {}
        for c in self.of_type["Case"]:
            acc = [nb for nb in G[c] if nb in self.of_type["Suspect"]]
            if len(acc) < 2:
                continue
            for i in range(len(acc)):
                a = self.cooff.setdefault(acc[i], {})
                for j in range(len(acc)):
                    if i != j:
                        a.setdefault(acc[j], []).append(c)
        self.assoc = {}
        for u, v, d in G.edges(data=True):
            if d.get("relationship") == "Known Associate":
                self.assoc.setdefault(u, set()).add(v)
                self.assoc.setdefault(v, set()).add(u)

        # Syndicates: membership and per-syndicate summary.
        self.syndicate_of = {}
        self.case_syndicate = {}
        self.syndicates = []
        self.syndicate_by_id = {}
        for syn in cache.get("syndicate_communities") or []:
            members = [m["id"] for m in syn.get("members", []) if m.get("id") in G]
            if not members:
                continue
            mset = set(members)
            case_hits = Counter()
            for m in members:
                self.syndicate_of[m] = syn["syndicate_id"]
                for nb in G[m]:
                    if nb in self.of_type["Case"]:
                        case_hits[nb] += 1
            shared = sorted(c for c, k in case_hits.items() if k >= 2)
            for c in shared:
                # A FIR belongs to a syndicate only when two or more of its members
                # are accused in it. One member's case is that person's, not the crew's.
                self.case_syndicate.setdefault(c, syn["syndicate_id"])
            all_cases = list(case_hits)
            districts = Counter(self.case.get(c, {}).get("district") for c in all_cases)
            crimes = Counter(self.case.get(c, {}).get("crime_type") for c in all_cases)
            years = [self.case.get(c, {}).get("year") for c in all_cases]
            years = [y for y in years if y]
            summary = {
                "syndicate_id": syn["syndicate_id"],
                "size": len(members),
                "risk_level": syn.get("risk_level"),
                "member_ids": members,
                "members": [{
                    "id": m,
                    "name": (self.person.get(m) or {}).get("name") or G.nodes[m].get("name") or m,
                    "risk": (self.person.get(m) or {}).get("risk"),
                    "rowdy": (self.person.get(m) or {}).get("rowdy", False),
                    "warrant": (self.person.get(m) or {}).get("warrant", False),
                    "gang": (self.person.get(m) or {}).get("gang"),
                } for m in members],
                "shared_case_ids": shared,
                "shared_cases": len(shared),
                "cases_total": len(all_cases),
                "open_cases": sum(1 for c in all_cases
                                  if (self.case.get(c, {}).get("status") or "") != "Closed"),
                "districts": [{"label": k, "count": v} for k, v in districts.most_common(6) if k],
                "crime_mix": [{"label": k, "count": v} for k, v in crimes.most_common(6) if k],
                "years": [min(years), max(years)] if years else None,
                "legacy_gangs": syn.get("legacy_gangs_represented") or [],
                "rowdy_members": sum(1 for m in members if (self.person.get(m) or {}).get("rowdy")),
                "warrant_members": sum(1 for m in members if (self.person.get(m) or {}).get("warrant")),
                "_mset": mset,
            }
            self.syndicates.append(summary)
            self.syndicate_by_id[syn["syndicate_id"]] = summary

        # Search tables. Lowercased once here rather than on every keystroke.
        self.search_people = []
        for sid in self.of_type["Suspect"]:
            p = self.person.get(sid) or {}
            name = p.get("name") or _s(G.nodes[sid].get("name")) or sid
            alias = (p.get("alias") or "").lower()
            gang = (p.get("gang") or _s(G.nodes[sid].get("gang")) or "").lower()
            self.search_people.append((sid, sid.lower(), name.lower(), alias, gang,
                                       f"{sid.lower()} {name.lower()} {alias} {gang}"))
        self.search_officers = [(o, o.lower(), (self.officer.get(o, {}).get("name")
                                                or _s(G.nodes[o].get("name")) or "").lower(),
                                 (self.officer.get(o, {}).get("station") or "").lower())
                                for o in self.of_type["Officer"]]
        self.search_vehicles = [(v, _norm_plate(v), (self.vehicle.get(v, {}).get("make") or "").lower())
                                for v in self.of_type["Vehicle"]]
        self.search_cyber = [(c, c.split(":", 1)[-1].lower(), c.split(":", 1)[0])
                             for c in self.of_type["CyberID"]]
        self.search_cases = [(c, (self.case.get(c) or {}).get("case_no") or "")
                             for c in self.of_type["Case"]]
        # Crime type / district / station facets -> their most serious, most recent FIRs.
        self.facets = {"crime_type": {}, "district": {}, "station": {}}
        for c in self.of_type["Case"]:
            info = self.case.get(c) or {}
            for f in ("crime_type", "district", "station"):
                v = info.get(f)
                if v:
                    self.facets[f].setdefault(v, []).append(c)
        for f, by in self.facets.items():
            for v, ids in by.items():
                ids.sort(key=lambda c: ((self.case.get(c) or {}).get("severity") or 0,
                                        (self.case.get(c) or {}).get("timestamp") or ""),
                         reverse=True)


def warm():
    """Build the index now rather than on the first request. Optional; call after
    load_and_build_intelligence() so the first search does not pay ~1 s."""
    _get()


def _index_key(G, cache):
    return (id(G), G.number_of_nodes() if G is not None else 0,
            G.number_of_edges() if G is not None else 0,
            id(cache.get("firs")), id(cache.get("criminals")),
            id(cache.get("syndicate_communities")), id(cache.get("officers")),
            id(cache.get("vehicles")), id(cache.get("cybercrime")))


_index = None
_index_lock = threading.Lock()


def _get():
    """(graph, cache, index) for this request, rebuilding the index if stale."""
    global _index
    G = _graph_getter()
    cache = _cache_getter() or {}
    if G is None:
        G = nx.Graph()
    key = _index_key(G, cache)
    idx = _index
    if idx is None or idx.key != key:
        with _index_lock:
            if _index is None or _index.key != key:
                _index = _Index(G, cache)
            idx = _index
    return G, cache, idx


# --------------------------------------------------------------------- node payload
def _label(G, idx, n):
    d = G.nodes[n]
    t = d.get("type")
    if t == "Suspect":
        return (idx.person.get(n) or {}).get("name") or _s(d.get("name")) or n
    if t == "Officer":
        return (idx.officer.get(n) or {}).get("name") or _s(d.get("name")) or n
    if t == "CyberID":
        return n.split(":", 1)[-1]
    return n


def _node(G, idx, n):
    """The node shape every endpoint returns. Every key is always present."""
    d = G.nodes[n]
    t = d.get("type") or "Other"
    out = {
        "id": n, "type": t, "label": _label(G, idx, n), "short": None, "sub": None,
        "district": None, "crime_type": None, "risk": None, "gang": None,
        "community": None, "rowdy": False, "warrant": False, "photo": None,
        "year": None, "status": None, "severity": None, "degree": G.degree(n),
    }
    if t == "Suspect":
        p = idx.person.get(n) or {}
        gang = p.get("gang") or _s(d.get("gang"))
        out.update(risk=p.get("risk") or _s(d.get("risk_level")), gang=gang,
                   rowdy=bool(p.get("rowdy")), warrant=bool(p.get("warrant")),
                   photo=p.get("photo"), community=idx.syndicate_of.get(n),
                   sub=f"{n} · {gang}" if gang else n)
    elif t == "Case":
        c = idx.case.get(n) or {}
        crime = c.get("crime_type") or _s(d.get("crime_type"))
        district = c.get("district") or _s(d.get("district"))
        out.update(district=district, crime_type=crime, year=c.get("year"),
                   status=c.get("status"), severity=c.get("severity"),
                   community=idx.case_syndicate.get(n),
                   short=f"FIR …{n[-5:]}" if len(n) > 8 else f"FIR {n}",
                   sub=" · ".join(x for x in (crime, district, str(c.get("year") or "")) if x))
    elif t == "Officer":
        o = idx.officer.get(n) or {}
        out.update(district=o.get("district"),
                   sub=" · ".join(x for x in (o.get("rank") or _s(d.get("rank")),
                                              o.get("station") or _s(d.get("station"))) if x) or n)
    elif t == "Vehicle":
        v = idx.vehicle.get(n) or {}
        out.update(district=v.get("theft_district"), status=v.get("recovery"),
                   sub=" · ".join(x for x in (v.get("make") or _s(d.get("make")),
                                              v.get("recovery") or _s(d.get("recovery"))) if x))
    elif t == "CyberID":
        kind = n.split(":", 1)[0]
        out.update(sub=f"{CYBER_KIND.get(kind, 'Identifier')} · {G.degree(n)} FIRs")
    if out["short"] is None:
        out["short"] = _short(out["label"])
    return out


# --------------------------------------------------------------------- filters
class _Filters:
    def __init__(self, args):
        raw = [t.strip() for t in (args.get("types") or "").split(",") if t.strip()]
        types = {_TYPE_ALIASES.get(t.lower(), t) for t in raw}
        self.types = {t for t in types if t in TYPES} or set(TYPES)
        self.district = _s(args.get("district"))
        self.crime_type = _s(args.get("crime_type"))
        self.year_from = _int(args.get("year_from"))
        self.year_to = _int(args.get("year_to"))
        self.rowdy = _truthy(args.get("rowdy"))
        self.warrant = _truthy(args.get("warrant"))

    @property
    def cases_hidden(self):
        return "Case" not in self.types

    @property
    def case_filtered(self):
        return bool(self.district or self.crime_type or self.year_from or self.year_to)

    def case_attrs_pass(self, idx, c):
        """The FIR-level filters alone, whether or not FIRs are drawn."""
        info = idx.case.get(c) or {}
        if self.district and (info.get("district") or "").lower() != self.district.lower():
            return False
        if self.crime_type and (info.get("crime_type") or "").lower() != self.crime_type.lower():
            return False
        y = info.get("year")
        if self.year_from and (not y or y < self.year_from):
            return False
        if self.year_to and (not y or y > self.year_to):
            return False
        return True

    def passes(self, G, idx, n):
        t = G.nodes[n].get("type")
        if t not in self.types:
            return False
        if t == "Case":
            return self.case_attrs_pass(idx, n)
        if t == "Suspect" and (self.rowdy or self.warrant):
            p = idx.person.get(n) or {}
            if self.rowdy and not p.get("rowdy"):
                return False
            if self.warrant and not p.get("warrant"):
                return False
        return True

    def describe(self):
        return {"types": sorted(self.types), "district": self.district,
                "crime_type": self.crime_type, "year_from": self.year_from,
                "year_to": self.year_to, "rowdy": self.rowdy, "warrant": self.warrant}


def _priority(G, idx, n, rel=None):
    """How much a neighbour is worth keeping when the cap bites. `rel` is the
    relationship of the edge that reached it; without one (search ranking) the
    node's type stands in."""
    t = G.nodes[n].get("type")
    p = REL_PRIORITY.get(rel, TYPE_PRIORITY.get(t, 5.0)) if rel else TYPE_PRIORITY.get(t, 5.0)
    if t == "Suspect":
        info = idx.person.get(n) or {}
        # Small next to the edge term: risk orders people, it does not outrank evidence.
        p += RISK_WEIGHT.get(info.get("risk") or _s(G.nodes[n].get("risk_level")), 0) * 1.5
        p += 2 if info.get("warrant") else 0
        p += 1 if info.get("rowdy") else 0
    elif t == "Case":
        info = idx.case.get(n) or {}
        p += (info.get("severity") or 0) / 25.0
        p += max(0, (info.get("year") or 2020) - 2020) / 3.0
    if t != "Officer":
        # An officer's degree is a caseload, not connectedness.
        p += 2.0 * math.log1p(G.degree(n))
    return p


# --------------------------------------------------------------------- expansion
def _neighbours(G, idx, u, filters, seeds=()):
    """(neighbour, relationship) pairs: recorded edges, plus co-accused through
    FIRs when FIRs are hidden.

    Hiding FIRs would otherwise empty a bipartite graph. Collapsing them into the
    person-to-person projection keeps the structure the investigator asked to see.
    """
    if u in idx.of_type["Officer"] and u not in seeds:
        # Never expand THROUGH an officer. Their caseload is not association, and a
        # FIR seed otherwise came back as its inspector's other 190 FIRs.
        return
    person = u in idx.of_type["Suspect"]
    adj = G[u]
    for v in adj:
        if person and filters.case_filtered and v in idx.of_type["Suspect"]:
            # A registry associate has no district or year. With FIR filters set,
            # people are reached only through FIRs that match them; otherwise a
            # "Mysuru" view fills with associates from anywhere in the state.
            continue
        yield v, adj[v].get("relationship")
    if filters.cases_hidden and person:
        for v, cids in (idx.cooff.get(u) or {}).items():
            if any(filters.case_attrs_pass(idx, c) for c in cids):
                yield v, "Co-accused"


def _expand(G, idx, seeds, depth, filters, max_nodes, preincluded=()):
    """Bounded breadth-first expansion. Returns (hop_by_node, stats)."""
    seeds = set(seeds)
    included = {s: 0 for s in seeds}
    for n in preincluded:
        included.setdefault(n, 0)
    frontier = list(included)
    considered = 0
    dropped = 0
    for hop in range(1, depth + 1):
        budget = max_nodes - len(included)
        if not frontier:
            break
        if budget <= 0:
            # The cap is spent with hops still to go: count what that leaves out.
            rest = {v for u in frontier for v, _r in _neighbours(G, idx, u, filters, seeds)
                    if v not in included and filters.passes(G, idx, v)}
            dropped += len(rest)
            considered += len(rest)
            break
        if hop < depth:
            # Keep room for the next hop. Spending the whole cap on hop 1 turned an
            # officer seed into 149 FIRs and nobody in them.
            budget = max(1, int(budget * HOP_SHARE))
        # A per-node share only matters while hops remain; on the final hop the
        # global ranking below already enforces the cap, and a share there dropped
        # neighbours of a hub even when the whole neighbourhood fitted.
        per_node = budget if hop >= depth else max(4, min(budget, (budget * 2) // max(1, len(frontier))))
        cand = {}
        for u in frontier:
            local = []
            seen = set()
            for v, rel in _neighbours(G, idx, u, filters, seeds):
                if v in included or v in seen or not filters.passes(G, idx, v):
                    continue
                seen.add(v)
                local.append((_priority(G, idx, v, rel), v))
            considered += len(local)
            if len(local) > per_node:
                dropped += len(local) - per_node
                local = heapq.nlargest(per_node, local)
            for p, v in local:
                if v in cand:
                    # Reached from two places: a bridge, the most informative kind
                    # of neighbour in link analysis.
                    cand[v] = (max(cand[v][0], p), cand[v][1] + 1)
                else:
                    cand[v] = (p, 1)
        ranked = sorted(cand.items(), key=lambda kv: kv[1][0] + 6.0 * (kv[1][1] - 1), reverse=True)
        chosen = [v for v, _ in ranked[:budget]]
        dropped += max(0, len(ranked) - budget)
        for v in chosen:
            included[v] = hop
        frontier = chosen
    return included, {"considered": considered, "dropped": dropped}


def _links_for(G, idx, nodes, filters):
    """Recorded edges among `nodes`, plus derived co-accused edges where needed."""
    nset = set(nodes)
    links = []
    for u, v, d in G.subgraph(nset).edges(data=True):
        rel = d.get("relationship") or "Linked"
        links.append({"source": u, "target": v, "relationship": rel,
                      "weight": RELATIONSHIP_WEIGHT.get(rel, 1.0), "derived": False})
    people = [n for n in nset if n in idx.of_type["Suspect"]]
    pset = set(people)
    for u in people:
        for v, cids in (idx.cooff.get(u) or {}).items():
            if v not in pset or not (u < v):
                continue
            ok = [c for c in cids if filters.case_attrs_pass(idx, c)]
            if not ok:
                continue
            off_screen = [c for c in ok if c not in nset]
            if not filters.cases_hidden and not off_screen:
                continue   # every shared FIR is already drawn between them
            links.append({"source": u, "target": v, "relationship": "Co-accused",
                          "weight": float(len(ok)), "derived": True,
                          "shared_cases": len(ok), "shared_cases_off_screen": len(off_screen),
                          "case_ids": ok[:6]})
    return links


def _predicted_in_view(G, idx, nodes, cache):
    """Adamic-Adar leads among the people on screen.

    Same projection as `analytics.build_link_prediction` (shared FIRs + recorded
    associates), scoped to the view. Two or more common associates are required:
    with one, every pair of a seed's associates would be "predicted" through the
    seed itself, which is a restatement of the view rather than a lead.
    """
    people = [n for n in nodes if n in idx.of_type["Suspect"]]
    pset = set(people)

    def adj(n):
        return set(idx.cooff.get(n) or ()) | (idx.assoc.get(n) or set())

    adj_cache = {}

    def A(n):
        if n not in adj_cache:
            adj_cache[n] = adj(n)
        return adj_cache[n]

    scores = {}
    for u in people:
        au = A(u)
        for w in au:
            aw = A(w)
            if len(aw) < 2 or len(aw) > 250:
                continue
            inc = 1.0 / math.log(len(aw))
            for x in aw:
                if x == u or x not in pset or not (u < x) or x in au:
                    continue
                s = scores.setdefault((u, x), [0.0, 0])
                s[0] += inc
                s[1] += 1
    ranked = sorted(((k, v) for k, v in scores.items() if v[1] >= 2),
                    key=lambda kv: kv[1][0], reverse=True)[:PREDICTED_IN_VIEW]
    global_top = {frozenset((r.get("a"), r.get("b"))) for r in (cache.get("predicted_links") or [])}
    out = [{"source": u, "target": x, "score": round(sc, 3), "common_associates": k,
            "in_global_top": frozenset((u, x)) in global_top}
           for (u, x), (sc, k) in ranked]
    # The global top list is computed over the whole register; surface any of its
    # pairs that happen to be on screen even if the local ranking did not reach them.
    have = {frozenset((p["source"], p["target"])) for p in out}
    for r in cache.get("predicted_links") or []:
        pair = frozenset((r.get("a"), r.get("b")))
        if r.get("a") in pset and r.get("b") in pset and pair not in have:
            out.append({"source": r["a"], "target": r["b"], "score": r.get("score"),
                        "common_associates": r.get("common_associates"), "in_global_top": True})
    return out


def _view_payload(G, idx, cache, hop_by_node, filters):
    nodes = list(hop_by_node)
    links = _links_for(G, idx, nodes, filters)
    H = nx.Graph()
    H.add_nodes_from(nodes)
    for l in links:
        H.add_edge(l["source"], l["target"], weight=l["weight"])
    try:
        score = nx.pagerank(H, weight="weight") if H.number_of_edges() else {}
    except Exception:
        # pagerank needs scipy; degree centrality is the stated fallback.
        score = nx.degree_centrality(H) if len(H) > 1 else {}
    top = max(score.values()) if score else 0
    out_nodes = []
    for n in nodes:
        p = _node(G, idx, n)
        p["hop"] = hop_by_node[n]
        p["view_degree"] = H.degree(n)
        p["score"] = round(score.get(n, 0) / top, 4) if top else 0.0
        out_nodes.append(p)
    by_type = Counter(p["type"] for p in out_nodes)
    communities = {p["community"] for p in out_nodes if p["community"]}
    return {
        "nodes": out_nodes,
        "links": links,
        "predicted": _predicted_in_view(G, idx, nodes, cache),
        "_stats": {
            "nodes": len(out_nodes), "links": len(links),
            "derived_links": sum(1 for l in links if l["derived"]),
            "by_type": dict(by_type), "communities": len(communities),
            "score": "PageRank within this view (weighted), scaled so the top node is 1",
        },
    }


# --------------------------------------------------------------------- search
def _field_score(tok, value):
    if not value or not tok:
        return 0
    if value == tok:
        return 100
    if value.startswith(tok):
        return 75
    if (" " + tok) in (" " + value) or ("-" + tok) in value or ("@" + tok) in value:
        return 60
    if len(tok) >= 3 and tok in value:
        return 35
    return 0


def _facet_score(toks, whole, value):
    """A facet is offered only on a deliberate match: the whole query opens the
    value, or a token is one of its words (or a long prefix of one). Otherwise
    "gang" offers Gangavathi PS and every short token offers a dozen stations."""
    if value.startswith(whole) and len(whole) >= 3:
        return 100 if value == whole else 80
    words = re.split(r"[\s()\-]+", value)
    for t in toks:
        if t in words:
            return 70
        if len(t) >= 5 and any(w.startswith(t) for w in words):
            return 60
    return 0


def _search(G, idx, q, limit=SEARCH_LIMIT):
    qn = re.sub(r"\s+", " ", (q or "").strip().lower())
    if not qn:
        return [], []
    toks = [t for t in re.split(r"[\s,]+", qn) if t]
    content = [t for t in toks if t not in _STOPWORDS]
    # De-duplicated and bounded: each token rescans the registers, so a pasted
    # paragraph must not tie up a worker thread.
    toks = list(dict.fromkeys(content or toks))[:8]
    digits = [t for t in toks if re.fullmatch(r"\d{3,}", t)]
    hits = []   # (matched_tokens, score, priority, id, matched_field)

    def add(n, matched, score, field):
        if matched:
            hits.append((matched, score, _priority(G, idx, n), n, field))

    whole = " ".join(toks)
    for sid, sid_l, name, alias, gang, hay in idx.search_people:
        if not any(t in hay for t in toks):
            continue   # one C-level scan rejects most of the register per keystroke
        matched = 0
        score = 0
        field = None
        for t in toks:
            best = max((_field_score(t, sid_l), "id"), (_field_score(t, name), "name"),
                       (int(_field_score(t, alias) * 0.9), "alias"),
                       (int(_field_score(t, gang) * 0.5), "gang"))
            if best[0]:
                matched += 1
                score += best[0]
                field = field or best[1]
        if matched and len(toks) > 1 and whole == name:
            score += 60
        add(sid, matched, score, field)

    for oid, oid_l, name, station in idx.search_officers:
        matched = score = 0
        field = None
        for t in toks:
            best = max((_field_score(t, oid_l), "kgid"), (_field_score(t, name), "name"),
                       (int(_field_score(t, station) * 0.4), "station"))
            if best[0]:
                matched += 1
                score += best[0]
                field = field or best[1]
        add(oid, matched, score, field)

    for plate, plate_n, make in idx.search_vehicles:
        matched = score = 0
        field = None
        for t in toks:
            best = max((_field_score(_norm_plate(t), plate_n), "plate"),
                       (int(_field_score(t, make) * 0.6), "make"))
            if best[0]:
                matched += 1
                score += best[0]
                field = field or best[1]
        if not matched and _norm_plate(qn) and _norm_plate(qn) in plate_n and len(_norm_plate(qn)) >= 4:
            matched, score, field = 1, 50, "plate"
        add(plate, matched, score, field)

    for nid, value, kind in idx.search_cyber:
        matched = score = 0
        for t in toks:
            s = _field_score(t, value)
            if s:
                matched += 1
                score += s
        add(nid, matched, score, "identifier")

    # FIRs by number: only digit tokens can name one, which keeps 18,000 ids off
    # the per-keystroke path for every name search.
    for t in digits:
        for cid, case_no in idx.search_cases:
            if cid == t or case_no == t:
                add(cid, 1, 100, "fir")
            elif cid.endswith(t) or case_no.endswith(t):
                add(cid, 1, 70 if len(t) >= 5 else 45, "fir")
            elif len(t) >= 6 and (t in cid):
                add(cid, 1, 35, "fir")

    # Crime type / district / station: offered as facets, plus their most serious
    # recent FIRs so a typed crime type is never a dead end.
    facets = []
    for field, label in (("crime_type", "Crime type"), ("district", "District"), ("station", "Station")):
        for value, ids in idx.facets[field].items():
            s = _facet_score(toks, whole, value.lower())
            if s:
                facets.append({"field": field, "label": label, "value": value,
                               "count": len(ids), "score": s})
                for cid in ids[:3]:
                    add(cid, 1, int(s * 0.45), field)
    facets.sort(key=lambda f: (-f["score"], f["field"] != "crime_type", -f["count"]))

    # Rank: tokens matched first (a two-word name beats a one-word gang match),
    # then match quality, then investigative priority.
    hits.sort(key=lambda h: (h[0], h[1], h[2]), reverse=True)
    best = {}
    for h in hits:
        if h[3] not in best:
            best[h[3]] = h
    ordered = list(best.values())
    caps = {"Suspect": 9, "Case": 6, "Officer": 4, "Vehicle": 4, "CyberID": 4}
    taken, counts, overflow = [], Counter(), []
    for h in ordered:
        t = G.nodes[h[3]].get("type")
        if counts[t] < caps.get(t, 4):
            taken.append(h)
            counts[t] += 1
        else:
            overflow.append(h)
        if len(taken) >= limit:
            break
    if len(taken) < limit:
        taken += overflow[: limit - len(taken)]
    taken.sort(key=lambda h: (h[0], h[1], h[2]), reverse=True)
    results = []
    for matched, score, _p, n, field in taken[:limit]:
        p = _node(G, idx, n)
        results.append({"id": n, "type": p["type"], "label": p["label"], "sub": p["sub"],
                        "risk": p["risk"], "rowdy": p["rowdy"], "warrant": p["warrant"],
                        "matched": field, "score": score, "tokens_matched": matched})
    return results, [{k: v for k, v in f.items() if k != "score"} for f in facets[:6]]


# --------------------------------------------------------------------- paths
def _rel_text(G, idx, a, b, rel):
    """One plain-English sentence for the edge a—b."""
    ta, tb = G.nodes[a].get("type"), G.nodes[b].get("type")
    la, lb = _label(G, idx, a), _label(G, idx, b)

    def fir(c):
        info = idx.case.get(c) or {}
        bits = [x for x in (info.get("crime_type"), info.get("district"),
                            str(info.get("year")) if info.get("year") else None) if x]
        return f"FIR {c}" + (f" ({', '.join(bits)})" if bits else "")

    pair = {ta: a, tb: b}
    if ta == tb == "Suspect":
        if rel == "Known Associate":
            return f"{la} and {lb} are recorded as known associates in the criminal registry."
        return f"{la} and {lb} are co-accused in one or more FIRs."
    if "Case" in pair and "Suspect" in pair and ta != tb:
        s, c = pair["Suspect"], pair["Case"]
        name = _label(G, idx, s)
        if rel == "Charged":
            return f"{name} is charged in {fir(c)}."
        if rel == "Co-Offender":
            return f"{name} is named as a co-offender in {fir(c)}."
        return f"{name} is linked to {fir(c)} ({rel.lower()})."
    if "Case" in pair and "Officer" in pair and ta != tb:
        o, c = pair["Officer"], pair["Case"]
        info = idx.officer.get(o) or {}
        rank = f", {info['rank']}" if info.get("rank") else ""
        return (f"{_label(G, idx, o)}{rank} is the investigating officer of {fir(c)} — an "
                "administrative link, not evidence of association.")
    if "Case" in pair and "Vehicle" in pair and ta != tb:
        v, c = pair["Vehicle"], pair["Case"]
        make = (idx.vehicle.get(v) or {}).get("make")
        return f"Vehicle {v}{f' ({make})' if make else ''} is recorded against {fir(c)}."
    if "Case" in pair and "CyberID" in pair and ta != tb:
        x, c = pair["CyberID"], pair["Case"]
        kind = CYBER_KIND.get(x.split(":", 1)[0], "Identifier")
        return f"{kind} {x.split(':', 1)[-1]} was traced in {fir(c)}."
    return f"{la} is linked to {lb} ({rel})."


def _paths(G, idx, src, dst, k, max_hops, via_officers):
    ends = {src, dst}
    officers = idx.of_type["Officer"]
    H = G if via_officers else nx.subgraph_view(
        G, filter_node=lambda n: n in ends or n not in officers)
    found, stopped = [], "exhausted"
    t0 = time.monotonic()
    try:
        for p in nx.shortest_simple_paths(H, src, dst):
            if len(p) - 1 > max_hops:
                stopped = "max_hops" if found else "no_path"
                break
            found.append(p)
            if len(found) >= k:
                stopped = "k_reached"
                break
            if time.monotonic() - t0 > PATH_TIME_BUDGET_S:
                stopped = "time_budget"
                break
    except nx.NetworkXNoPath:
        stopped = "no_path"
    except nx.NodeNotFound:
        stopped = "no_path"
    return found, stopped, round((time.monotonic() - t0) * 1000, 1)


def _path_summary(G, p):
    mid = Counter(G.nodes[n].get("type") for n in p[1:-1])
    words = {"Case": ("FIR", "FIRs"), "Suspect": ("person", "people"),
             "CyberID": ("shared identifier", "shared identifiers"),
             "Vehicle": ("vehicle", "vehicles"), "Officer": ("officer", "officers")}
    parts = [f"{c} {words.get(t, (t.lower(), t.lower() + 's'))[0 if c == 1 else 1]}"
             for t, c in mid.most_common()]
    hops = len(p) - 1
    if not parts:
        return "Directly linked (1 hop)."
    return f"{hops} hops through " + ", ".join(parts) + "."


# --------------------------------------------------------------------- profile
def _scope_for_locations(locations, ident):
    if ident["scope"]["level"] == "state":
        return True
    return any(auth.in_jurisdiction(d, st, ident) for d, st in locations)


def _counter_list(values, n=5):
    return [{"label": k, "count": v} for k, v in Counter(x for x in values if x).most_common(n)]


def _profile(G, idx, cache, n, ident):
    t = G.nodes[n].get("type")
    base = _node(G, idx, n)
    nbrs = list(G[n])
    by_type = Counter(G.nodes[v].get("type") for v in nbrs)
    prof = {"entity": base, "neighbours_by_type": dict(by_type), "links": []}
    case_nbrs = [v for v in nbrs if v in idx.of_type["Case"]]
    locations = []

    def case_rows(cids, limit=8):
        rows = []
        for c in sorted(cids, key=lambda c: (idx.case.get(c) or {}).get("timestamp") or "",
                        reverse=True)[:limit]:
            info = idx.case.get(c) or {}
            rel = (G.get_edge_data(n, c) or {}).get("relationship")
            rows.append({"id": c, "crime_type": info.get("crime_type"),
                         "district": info.get("district"), "year": info.get("year"),
                         "status": info.get("status"), "relationship": rel})
        return rows

    if t == "Suspect":
        p = idx.person.get(n) or {}
        infos = [idx.case.get(c) or {} for c in case_nbrs]
        locations = [(i.get("district"), i.get("station")) for i in infos]
        co = sorted(((v, len(c)) for v, c in (idx.cooff.get(n) or {}).items()),
                    key=lambda x: (-x[1], x[0]))
        syn_id = idx.syndicate_of.get(n)
        syn = idx.syndicate_by_id.get(syn_id) if syn_id else None
        years = [i.get("year") for i in infos if i.get("year")]
        prof["suspect"] = {
            "name": base["label"], "alias": p.get("alias"), "gang": base["gang"],
            "risk_level": base["risk"], "photo_url": p.get("photo"),
            "flags": {"rowdy_sheeter": bool(p.get("rowdy")),
                      "history_sheet": bool(p.get("history_sheet")),
                      "active_warrant": bool(p.get("warrant"))},
            "past_fir_count": p.get("past_fir_count"), "past_arrests": p.get("past_arrests"),
            "cases_count": len(case_nbrs),
            "open_cases": sum(1 for i in infos if (i.get("status") or "") != "Closed"),
            "crime_types": _counter_list(i.get("crime_type") for i in infos),
            "districts": _counter_list(i.get("district") for i in infos),
            "active_years": [min(years), max(years)] if years else None,
            "co_offenders": [{"id": v, "name": _label(G, idx, v), "shared_cases": k,
                              "risk": (idx.person.get(v) or {}).get("risk")} for v, k in co[:8]],
            "co_offenders_total": len(co),
            "known_associates": [{"id": v, "name": _label(G, idx, v)}
                                 for v in sorted(idx.assoc.get(n) or ())[:8]],
            "known_associates_total": len(idx.assoc.get(n) or ()),
            "syndicate": ({"id": syn["syndicate_id"], "size": syn["size"],
                           "risk_level": syn["risk_level"]} if syn else None),
            "recent_cases": case_rows(case_nbrs, 6),
        }
        prof["recidivism"] = _recidivism(cache, n, ident, locations)
        prof["links"] = [{"label": "Offender dossier", "href": f"/suspect?id={quote(n)}"},
                         {"label": "Show on map", "href": f"/map?track={quote(n)}"}]
    elif t == "Case":
        info = idx.case.get(n) or {}
        locations = [(info.get("district"), info.get("station"))]
        accused = [{"id": v, "name": _label(G, idx, v),
                    "relationship": (G.get_edge_data(n, v) or {}).get("relationship"),
                    "risk": (idx.person.get(v) or {}).get("risk"),
                    "warrant": bool((idx.person.get(v) or {}).get("warrant"))}
                   for v in nbrs if v in idx.of_type["Suspect"]]
        officer = next(({"id": v, "name": _label(G, idx, v),
                         "rank": (idx.officer.get(v) or {}).get("rank")}
                        for v in nbrs if v in idx.of_type["Officer"]), None)
        fraud = idx.cyber_case.get(n)
        prof["case"] = {
            "crime_type": info.get("crime_type"), "district": info.get("district"),
            "station": info.get("station"), "status": info.get("status"),
            "registered": info.get("timestamp"), "severity": info.get("severity"),
            "sections": info.get("sections"), "accused": accused, "officer": officer,
            "vehicles": [v for v in nbrs if v in idx.of_type["Vehicle"]],
            "identifiers": [v for v in nbrs if v in idx.of_type["CyberID"]],
            "fraud_amount": fraud[0] if fraud else None,
            "syndicate": idx.case_syndicate.get(n),
        }
        prof["links"] = [{"label": "Case file", "href": f"/case?id={quote(n)}"}]
    elif t == "Officer":
        o = idx.officer.get(n) or {}
        infos = [idx.case.get(c) or {} for c in case_nbrs]
        locations = [(o.get("district"), o.get("station"))]
        prof["officer"] = {
            "kgid": n, "name": base["label"], "rank": o.get("rank"),
            "station": o.get("station"), "district": o.get("district"),
            "specialization": o.get("specialization"), "cases_investigated": len(case_nbrs),
            "open_cases": sum(1 for i in infos if (i.get("status") or "") != "Closed"),
            "crime_types": _counter_list(i.get("crime_type") for i in infos),
            "districts": _counter_list(i.get("district") for i in infos),
        }
        prof["links"] = [{"label": "Officer registry",
                          "href": f"/intel?tab=officers&q={quote(base['label'])}"}]
    elif t == "Vehicle":
        v = idx.vehicle.get(n) or {}
        infos = [idx.case.get(c) or {} for c in case_nbrs]
        locations = [(i.get("district"), i.get("station")) for i in infos]
        prof["vehicle"] = {
            "plate": n, "make": v.get("make"), "recovery_status": v.get("recovery"),
            "theft_district": v.get("theft_district"), "theft_date": v.get("theft_date"),
            "cases": case_rows(case_nbrs),
        }
        prof["links"] = [{"label": "Vehicle registry",
                          "href": f"/intel?tab=vehicles&q={quote(n)}"}]
    elif t == "CyberID":
        kind, value = (n.split(":", 1) + [""])[:2]
        infos = [idx.case.get(c) or {} for c in case_nbrs]
        locations = [(i.get("district"), i.get("station")) for i in infos]
        frauds = [idx.cyber_case.get(c) for c in case_nbrs if idx.cyber_case.get(c)]
        accused = Counter(s for c in case_nbrs for s in G[c] if s in idx.of_type["Suspect"])
        prof["identifier"] = {
            "kind": CYBER_KIND.get(kind, kind), "value": value, "cases_count": len(case_nbrs),
            "total_fraud": round(sum(f[0] for f in frauds)) if frauds else 0,
            "victims": sum(f[1] for f in frauds) if frauds else 0,
            "districts": _counter_list(i.get("district") for i in infos),
            "accused": [{"id": s, "name": _label(G, idx, s), "cases": k}
                        for s, k in accused.most_common(8)],
            "cases": case_rows(case_nbrs),
        }
        prof["links"] = [{"label": "Cyber registry",
                          "href": f"/intel?tab=cybercrime&q={quote(value)}"}]

    in_scope = _scope_for_locations(locations, ident)
    prof["jurisdiction"] = {"in_scope": in_scope, "scope": dict(ident["scope"])}
    if not in_scope:
        # Permitted, and conspicuous: the same rule main.py applies to dossiers.
        audit.record("out_of_jurisdiction_read", "network_entity", n,
                     reason=audit.stated_reason(),
                     extra={"scope_level": ident["scope"]["level"],
                            "scope_district": ident["scope"]["district"],
                            "scope_station": ident["scope"]["station"]})
        prof["jurisdiction"]["audited"] = True
        prof["jurisdiction"]["note"] = ("This entity is outside your jurisdiction. You may "
                                        "read it, and the access has been recorded "
                                        "separately for review.")
    return prof


def _recidivism(cache, sid, ident, locations):
    rec = cache.get("recidivism") or {}
    scores = rec.get("scores") or {}
    if not (auth.has_permission("watchlist_names", ident)
            and _scope_for_locations(locations, ident)):
        return {"withheld": True, "required_permission": "watchlist_names",
                "note": "The recidivism index is part of the named watchlist and is shown "
                        "only to roles holding watchlist_names for this person's jurisdiction."}
    if sid not in scores:
        return {"withheld": False, "available": False,
                "note": "Not scored: the person is outside the model's observation panel."}
    idx_val = float(scores[sid])
    # Same bands as ml/tasks/recidivism.py `_band`.
    band = ("Extreme" if idx_val >= 95 else "High" if idx_val >= 80
            else "Elevated" if idx_val >= 50 else "Moderate")
    return {"withheld": False, "available": True, "index": idx_val, "band": band,
            "meaning": rec.get("index_meaning"),
            "note": "A ranking instrument for supervisory attention, not a statement "
                    "about the person. Not for bail, sentencing or custody decisions."}


# --------------------------------------------------------------------- routes
API = "/serverless/api/network"


def _parse_max(args, default):
    n = _int(args.get("max_nodes"), default)
    return max(10, min(MAX_NODES_CEILING, n or default))


def _missing(n):
    return _json({"error": "unknown_entity", "id": n,
                  "hint": "GET /serverless/api/network/search?q= to find an entity id."}, 404)


@bp.route(f"{API}/search", methods=["GET", "OPTIONS"])
@audit.audited("search_network")
@auth.require_role("investigator")
def route_search():
    if request.method == "OPTIONS":
        return _ok_preflight()
    G, _cache, idx = _get()
    q = (request.args.get("q") or "").strip()[:120]
    limit = max(1, min(SEARCH_LIMIT, _int(request.args.get("limit"), SEARCH_LIMIT)))
    if not q:
        return _json({"query": q, "results": [], "facets": []})
    results, facets = _search(G, idx, q, limit)
    return _json({"query": q, "results": results, "facets": facets})


@bp.route(f"{API}/explore", methods=["GET", "OPTIONS"])
@audit.audited("explore_network")
@auth.require_role("investigator")
def route_explore():
    if request.method == "OPTIONS":
        return _ok_preflight()
    G, cache, idx = _get()
    args = request.args
    filters = _Filters(args)
    depth = max(1, min(2, _int(args.get("depth"), 2) or 2))
    seeds = [s.strip() for s in (args.get("seed") or "").split(",") if s.strip()][:MAX_SEEDS]
    syndicate = _s(args.get("syndicate"))
    q = (args.get("q") or "").strip()[:120]
    focus = None
    pre = []

    if syndicate:
        syn = idx.syndicate_by_id.get(syndicate)
        if not syn:
            return _missing(syndicate)
        seeds = list(syn["member_ids"])
        pre = [c for c in syn["shared_case_ids"] if filters.passes(G, idx, c)]
        depth = max(1, min(2, _int(args.get("depth"), 1) or 1))
        focus = {"kind": "syndicate", "id": syndicate, "size": syn["size"],
                 "risk_level": syn["risk_level"]}
    elif seeds:
        unknown = [s for s in seeds if s not in G]
        if unknown:
            return _missing(unknown[0])
        focus = {"kind": "seed", "ids": seeds}
    elif q:
        results, _f = _search(G, idx, q, SEARCH_LIMIT)
        if not results:
            return _json({"nodes": [], "links": [], "predicted": [], "truncated": False,
                          "focus": {"kind": "query", "q": q, "ids": []},
                          "stats": {"nodes": 0, "links": 0},
                          "note": f"Nothing in the graph matches “{q}”."})
        top = results[0]
        if top["score"] >= 90 or len(results) == 1:
            seeds = [top["id"]]
        else:
            # An ambiguous query seeds from the best few of the top result's kind —
            # "Bawaria Gang" means its members, not one arbitrary member.
            seeds = [r["id"] for r in results
                     if r["type"] == top["type"]
                     and r["tokens_matched"] == top["tokens_matched"]][:5]
        focus = {"kind": "query", "q": q, "ids": seeds}

    if not seeds:
        return _json(_overview(G, idx, cache, filters, _parse_max(args, OVERVIEW_NODES)))

    # A syndicate's members and their shared FIRs are the subject of the view; the cap
    # cannot be below them, and stats.cap must say what was actually applied.
    max_nodes = max(_parse_max(args, DEFAULT_SEEDED_NODES), len(set(seeds) | set(pre or ())))
    hop_by_node, ex = _expand(G, idx, seeds, depth, filters, max_nodes, preincluded=pre)
    view = _view_payload(G, idx, cache, hop_by_node, filters)
    stats = view.pop("_stats")
    truncated = ex["dropped"] > 0
    stats.update(cap=max_nodes, depth=depth, considered=ex["considered"], left_out=ex["dropped"])
    note = None
    if truncated:
        note = (f"Capped at {max_nodes} nodes: {ex['dropped']} further neighbours were left "
                "out, lowest priority first (officer links, then registry associates, then "
                "low-risk, low-degree entities). "
                "Expand a node to see more of its neighbourhood.")
    # The decorator records that the graph was read; this records whose.
    audit.record("network_subjects", "network_entity", seeds[0],
                 reason=audit.stated_reason(), extra={"subjects": seeds, "via": "explore"})
    return _json({**view, "truncated": truncated, "stats": stats, "focus": focus,
                  "filters": filters.describe(), "note": note})


def _overview(G, idx, cache, filters, max_nodes):
    """No seed: the largest syndicates with the FIRs their members share."""
    case_filtered = filters.case_filtered
    included = {}
    shown, skipped = [], 0
    for syn in idx.syndicates:
        cases = [c for c in syn["shared_case_ids"] if filters.case_attrs_pass(idx, c)]
        if case_filtered and not cases:
            continue   # nothing this crew did matches the FIR filters
        members = [m for m in syn["member_ids"] if filters.passes(G, idx, m)]
        if not members:
            continue
        add = members + ([c for c in cases] if not filters.cases_hidden else [])
        add = [x for x in add if x not in included]
        if len(included) + len(add) > max_nodes:
            skipped += 1
            continue
        for x in add:
            included[x] = 0
        shown.append(syn["syndicate_id"])
    view = _view_payload(G, idx, cache, included, filters)
    stats = view.pop("_stats")
    total = len(idx.syndicates)
    stats.update(cap=max_nodes, syndicates_shown=len(shown), syndicates_total=total)
    truncated = len(shown) < total
    note = (f"Overview: the {len(shown)} largest of {total} detected syndicates (Louvain over "
            "repeat co-offending, two or more shared FIRs) with the FIRs their members share, "
            f"capped at {max_nodes} nodes. Search for an entity or pick a syndicate to go deeper.")
    return {**view, "truncated": truncated, "stats": stats,
            "focus": {"kind": "overview", "syndicates": shown},
            "filters": filters.describe(), "note": note}


@bp.route(f"{API}/path", methods=["GET", "OPTIONS"])
@audit.audited("read_network_path")
@auth.require_role("investigator")
def route_path():
    if request.method == "OPTIONS":
        return _ok_preflight()
    G, cache, idx = _get()
    src = (request.args.get("from") or "").strip()
    dst = (request.args.get("to") or "").strip()
    if not src or not dst:
        return _json({"error": "from_and_to_required"}, 400)
    if src == dst:
        return _json({"error": "from_equals_to"}, 400)
    for n in (src, dst):
        if n not in G:
            return _missing(n)
    k = max(1, min(PATH_K_MAX, _int(request.args.get("k"), 3) or 3))
    max_hops = max(1, min(PATH_MAX_HOPS, _int(request.args.get("max_hops"), 8) or 8))
    via_officers = _truthy(request.args.get("via_officers"))
    found, stopped, ms = _paths(G, idx, src, dst, k, max_hops, via_officers)

    paths = []
    on_path = {}
    links = {}
    for p in found:
        hops = []
        for a, b in zip(p, p[1:]):
            rel = (G.get_edge_data(a, b) or {}).get("relationship") or "Linked"
            hops.append({"from": a, "to": b, "relationship": rel,
                         "from_label": _label(G, idx, a), "to_label": _label(G, idx, b),
                         "explanation": _rel_text(G, idx, a, b, rel)})
            links[frozenset((a, b))] = {"source": a, "target": b, "relationship": rel,
                                        "weight": RELATIONSHIP_WEIGHT.get(rel, 1.0),
                                        "derived": False}
        for i, n in enumerate(p):
            on_path.setdefault(n, i)
        paths.append({"length": len(p) - 1, "nodes": p, "hops": hops,
                      "summary": _path_summary(G, p)})

    nodes = []
    for n, pos in on_path.items():
        payload = _node(G, idx, n)
        payload.update(hop=pos, view_degree=0, score=1.0 if n in (src, dst) else 0.5)
        nodes.append(payload)
    reasons = {
        "k_reached": f"Stopped after the {k} shortest paths.",
        "max_hops": f"No further path within {max_hops} hops.",
        "time_budget": "Stopped at the time budget; longer alternatives may exist.",
        "exhausted": "These are all the simple paths between the two.",
        "no_path": (f"No connection within {max_hops} hops"
                    + ("" if via_officers else " without passing through an investigating "
                       "officer") + "."),
    }
    note = reasons.get(stopped)
    if not via_officers:
        note += (" Paths through investigating officers are excluded: shared caseload is not "
                 "association. Pass via_officers=1 to include them.")
    audit.record("network_subjects", "network_entity", src, reason=audit.stated_reason(),
                 extra={"subjects": [src, dst], "via": "path"})
    return _json({
        "from": _node(G, idx, src), "to": _node(G, idx, dst), "paths": paths,
        "graph": {"nodes": nodes, "links": list(links.values())},
        "search": {"k": k, "max_hops": max_hops, "via_officers": via_officers,
                   "elapsed_ms": ms, "stopped": stopped,
                   "method": "Yen's k shortest simple paths (networkx.shortest_simple_paths), "
                             "unweighted hop count"},
        "note": note,
    })


@bp.route(f"{API}/entity/<path:entity_id>", methods=["GET", "OPTIONS"])
@audit.audited("read_network_entity", subject_type="network_entity", subject_arg="entity_id")
@auth.require_role("investigator")
def route_entity(entity_id):
    if request.method == "OPTIONS":
        return _ok_preflight()
    G, cache, idx = _get()
    n = (entity_id or "").strip()
    if n not in G:
        return _missing(n)
    return _json(_profile(G, idx, cache, n, auth.current_identity()))


@bp.route(f"{API}/meta", methods=["GET", "OPTIONS"])
@audit.audited("read_network_meta")
@auth.require_role("investigator")
def route_meta():
    """Filter vocabularies, read from the corpus so the UI never hardcodes a list
    that can drift from it (the officers page once offered two commissionerates
    as districts and omitted twenty-three real ones)."""
    if request.method == "OPTIONS":
        return _ok_preflight()
    G, _cache, idx = _get()
    years = [c.get("year") for c in idx.case.values() if c.get("year")]
    return _json({
        "types": [{"type": t, "count": len(idx.of_type[t])} for t in TYPES],
        "districts": sorted(idx.facets["district"]),
        "crime_types": sorted(idx.facets["crime_type"]),
        "years": [min(years), max(years)] if years else None,
        "graph": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
        "syndicates": len(idx.syndicates),
        "limits": {"max_nodes": MAX_NODES_CEILING, "default_seeded": DEFAULT_SEEDED_NODES,
                   "overview": OVERVIEW_NODES, "max_seeds": MAX_SEEDS,
                   "path_k": PATH_K_MAX, "path_max_hops": PATH_MAX_HOPS},
    })


@bp.route(f"{API}/syndicates", methods=["GET", "OPTIONS"])
@audit.audited("read_network_syndicates")
@auth.require_role("investigator")
def route_syndicates():
    if request.method == "OPTIONS":
        return _ok_preflight()
    _G, _cache, idx = _get()
    q = (request.args.get("q") or "").strip().lower()
    district = _s(request.args.get("district"))
    crime = _s(request.args.get("crime_type"))
    limit = max(1, min(500, _int(request.args.get("limit"), 250) or 250))
    out = []
    for syn in idx.syndicates:
        if district and not any(d["label"].lower() == district.lower() for d in syn["districts"]):
            continue
        if crime and not any(c["label"].lower() == crime.lower() for c in syn["crime_mix"]):
            continue
        if q and not (q in syn["syndicate_id"].lower()
                      or any(q in (m["name"] or "").lower() or q in m["id"].lower()
                             or q in (m["gang"] or "").lower() for m in syn["members"])):
            continue
        out.append({k: v for k, v in syn.items() if not k.startswith("_")
                    and k != "shared_case_ids"})
    return _json({
        "syndicates": out[:limit], "total": len(out), "total_detected": len(idx.syndicates),
        "method": "Louvain communities over the co-offender graph, keeping only pairs who "
                  "share two or more FIRs (main.py / analytics.detect_crime_communities). "
                  "Districts and crime mix count the members' FIRs; a shared FIR has two "
                  "or more members accused in it.",
    })
