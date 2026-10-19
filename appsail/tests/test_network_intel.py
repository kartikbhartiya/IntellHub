"""The link-analysis workbench: bounds, correctness of what it draws, and the gates.

The properties pinned here are the ones a link chart can get quietly wrong:

* **Bounds.** A hub seed (an inspector with 250 FIRs) must stay under the cap the
  caller asked for and say that it left things out — never return the hub's whole
  neighbourhood, and never truncate silently.
* **Every drawn edge is real.** A recorded link must be an edge in the knowledge
  graph with the same relationship; a derived co-accused link must be backed by the
  shared FIRs it claims; a path must be a walk along actual edges.
* **Gates.** Every route refuses anonymous callers, and the recidivism index does
  not leak through the side panel to a role the watchlist itself would refuse.
"""
import os
import sys

import networkx as nx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit  # noqa: E402
import auth  # noqa: E402
import main  # noqa: E402
import network_intel as NI  # noqa: E402

API = "/serverless/api/network"


@pytest.fixture(scope="module", autouse=True)
def loaded():
    if "network_intel" not in main.app.blueprints:
        main.app.register_blueprint(NI.bp)
    NI.configure(lambda: main.knowledge_graph, lambda: main.data_cache)
    main.load_and_build_intelligence()


@pytest.fixture(scope="module")
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def _login(client, username, password):
    r = client.post("/serverless/api/auth/login",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return {"Authorization": f"Bearer {r.get_json()['token']}"}


@pytest.fixture(scope="module")
def io(client):
    return _login(client, "io", "scrb-io")


@pytest.fixture(scope="module")
def sp(client):
    return _login(client, "sp", "scrb-sp")


def G():
    return main.knowledge_graph


def _get(client, path, headers):
    r = client.get(API + path, headers=headers)
    return r.status_code, r.get_json()


def _hub(kind="Officer"):
    g = G()
    return max((n for n, d in g.nodes(data=True) if d.get("type") == kind), key=g.degree)


def _assert_links_are_real(body):
    """Every recorded link is a graph edge with that relationship; every derived
    link is backed by FIRs both people are actually accused in."""
    g = G()
    ids = {n["id"] for n in body["nodes"]}
    for link in body["links"]:
        a, b = link["source"], link["target"]
        assert a in ids and b in ids, "a link points at a node that is not in the view"
        if link["derived"]:
            assert g.nodes[a]["type"] == g.nodes[b]["type"] == "Suspect"
            shared = {c for c in g[a] if g.nodes[c].get("type") == "Case"} & set(g[b])
            assert len(shared) >= link["weight"] >= 1
            assert set(link["case_ids"]) <= shared
        else:
            assert g.has_edge(a, b), f"{a}—{b} is drawn but is not in the graph"
            assert g[a][b].get("relationship") == link["relationship"]


# ------------------------------------------------------------------ gates
ROUTES = ["/search?q=ravi", "/explore", "/explore?seed=SUS-1001",
          "/path?from=SUS-1001&to=SUS-8286", "/entity/SUS-1001", "/syndicates", "/meta"]


@pytest.mark.parametrize("path", ROUTES)
def test_every_route_refuses_anonymous_callers(client, path):
    status, body = _get(client, path, {})
    assert status == 403
    assert body["error"] == "insufficient_role"


@pytest.mark.parametrize("path", ROUTES)
def test_every_route_refuses_the_analyst_role(client, path):
    headers = {"Authorization": "Bearer " + auth.make_token("t-analyst", "analyst")}
    status, _ = _get(client, path, headers)
    assert status == 403


def test_preflight_is_not_gated(client):
    r = client.open(API + "/explore", method="OPTIONS")
    assert r.status_code == 204


# ------------------------------------------------------------------ search
def test_search_finds_a_person_by_full_name_first(client, io):
    status, body = _get(client, "/search?q=Kartik Bhartiya", io)
    assert status == 200
    assert body["results"][0]["id"] == "SUS-1001"
    assert body["results"][0]["type"] == "Suspect"


def test_search_by_exact_ids_of_every_kind(client, io):
    g = G()
    case = next(n for n, d in g.nodes(data=True) if d.get("type") == "Case")
    officer = next(n for n, d in g.nodes(data=True) if d.get("type") == "Officer")
    cyber = next(n for n, d in g.nodes(data=True) if d.get("type") == "CyberID")
    for q, want in (("SUS-1001", "SUS-1001"), (case, case), (officer, officer),
                    (cyber.split(":", 1)[1], cyber)):
        _s, body = _get(client, f"/search?q={q}", io)
        assert body["results"][0]["id"] == want, q


def test_search_matches_a_plate_however_it_is_typed(client, io):
    plate = next(n for n, d in G().nodes(data=True) if d.get("type") == "Vehicle")
    typed = plate.replace("-", " ").lower()
    _s, body = _get(client, f"/search?q={typed}", io)
    assert plate in [r["id"] for r in body["results"]]


def test_search_finds_a_fir_by_the_tail_of_its_number(client, io):
    # The last nine digits are the CaseNo, which repeats across stations, so use
    # the shortest tail that names exactly one FIR.
    cases = [n for n, d in G().nodes(data=True) if d.get("type") == "Case"]
    case = cases[0]
    tail = next(case[-k:] for k in range(9, len(case) + 1)
                if sum(1 for c in cases if c.endswith(case[-k:])) == 1)
    _s, body = _get(client, f"/search?q={tail}", io)
    assert body["results"][0]["id"] == case


def test_search_offers_a_crime_type_facet_without_junk(client, io):
    _s, body = _get(client, "/search?q=extortion", io)
    assert {"field": "crime_type", "value": "Extortion"}.items() <= body["facets"][0].items()
    _s, body = _get(client, "/search?q=bawaria gang", io)
    assert not any(f["field"] == "station" for f in body["facets"]), body["facets"]
    assert all(r["type"] == "Suspect" for r in body["results"][:5])


def test_search_is_bounded_and_empty_query_is_empty(client, io):
    _s, body = _get(client, "/search?q=a", io)
    assert len(body["results"]) <= NI.SEARCH_LIMIT
    _s, body = _get(client, "/search?q=", io)
    assert body["results"] == []


# ------------------------------------------------------------------ explore
def test_overview_is_capped_and_says_so(client, io):
    status, body = _get(client, "/explore", io)
    assert status == 200
    assert 0 < len(body["nodes"]) <= NI.OVERVIEW_NODES
    assert body["focus"]["kind"] == "overview"
    assert body["truncated"] is True
    assert body["stats"]["syndicates_shown"] < body["stats"]["syndicates_total"]
    assert "largest" in body["note"]
    # Every person shown belongs to the syndicate the overview drew them for.
    people = [n for n in body["nodes"] if n["type"] == "Suspect"]
    assert people and all(n["community"] for n in people)
    _assert_links_are_real(body)


@pytest.mark.parametrize("cap", [20, 60, 150, 500])
def test_a_hub_seed_stays_under_the_cap(client, io, cap):
    hub = _hub("Officer")
    assert G().degree(hub) > 150, "the corpus no longer has a hub to test against"
    status, body = _get(client, f"/explore?seed={hub}&depth=2&max_nodes={cap}", io)
    assert status == 200
    assert len(body["nodes"]) <= cap
    assert hub in {n["id"] for n in body["nodes"]}
    assert body["truncated"] is True and body["stats"]["left_out"] > 0
    assert body["note"] and str(cap) in body["note"]
    _assert_links_are_real(body)


def test_max_nodes_is_clamped_to_the_ceiling(client, io):
    _s, body = _get(client, f"/explore?seed={_hub()}&depth=2&max_nodes=100000", io)
    assert len(body["nodes"]) <= NI.MAX_NODES_CEILING
    assert body["stats"]["cap"] == NI.MAX_NODES_CEILING


def test_depth_two_from_a_hub_reaches_beyond_its_firs(client, io):
    """Hop 1 may not spend the whole budget: an officer view must reach people."""
    _s, body = _get(client, f"/explore?seed={_hub()}&depth=2&max_nodes=150", io)
    assert any(n["type"] == "Suspect" for n in body["nodes"])


def test_a_fir_seed_is_not_flooded_by_its_officers_caseload(client, io):
    """Expansion never passes THROUGH an officer who is not the seed."""
    g = G()
    case = next(n for n, d in g.nodes(data=True) if d.get("type") == "Case"
                and any(g.nodes[x]["type"] == "Officer" for x in g[n]))
    _s, body = _get(client, f"/explore?seed={case}&depth=2", io)
    hop = {n["id"]: n["hop"] for n in body["nodes"]}
    for n, h in hop.items():
        if h == 2:
            parents = [p for p in g[n] if hop.get(p) == 1]
            assert any(g.nodes[p]["type"] != "Officer" for p in parents), n


def test_a_gang_query_keeps_the_firs_that_join_its_members(client, io):
    _s, body = _get(client, "/explore?q=Bawaria Gang", io)
    assert any(n["type"] == "Case" for n in body["nodes"])
    seeds = set(body["focus"]["ids"])
    g = G()
    for s in seeds:
        firs = [c for c in g[s] if g.nodes[c]["type"] == "Case"]
        on = [c for c in firs if c in {n["id"] for n in body["nodes"]}]
        assert on or not firs, f"{s}: none of its {len(firs)} FIRs survived the cap"


def test_depth_one_returns_only_the_seed_and_its_neighbours(client, io):
    g = G()
    _s, body = _get(client, "/explore?seed=SUS-1001&depth=1", io)
    ids = {n["id"] for n in body["nodes"]}
    assert ids <= {"SUS-1001"} | set(g["SUS-1001"])
    assert ids == {"SUS-1001"} | set(g["SUS-1001"]), "a small neighbourhood should be complete"
    assert body["truncated"] is False
    seed = next(n for n in body["nodes"] if n["id"] == "SUS-1001")
    assert seed["hop"] == 0 and 0 <= seed["score"] <= 1


def test_node_shape_is_complete(client, io):
    _s, body = _get(client, "/explore?seed=SUS-1001", io)
    keys = {"id", "type", "label", "sub", "district", "crime_type", "risk", "gang", "degree",
            "score", "community", "rowdy", "warrant", "photo", "hop", "view_degree"}
    for n in body["nodes"]:
        assert keys <= set(n), keys - set(n)


def test_hiding_firs_collapses_to_the_person_network(client, io):
    _s, body = _get(client, "/explore?seed=SUS-1001&types=Suspect", io)
    assert {n["type"] for n in body["nodes"]} == {"Suspect"}
    assert any(link["derived"] for link in body["links"])
    _assert_links_are_real(body)


def test_derived_links_never_duplicate_firs_on_screen(client, io):
    _s, body = _get(client, "/explore?seed=SUS-1001&depth=2", io)
    ids = {n["id"] for n in body["nodes"]}
    for link in body["links"]:
        if link["derived"]:
            assert any(c not in ids for c in link["case_ids"]) or link["shared_cases_off_screen"]


def test_fir_filters_apply_to_every_fir_and_stop_registry_hops(client, io):
    g = G()

    def mysuru_firs(s):
        return sum(1 for c in g[s] if g.nodes[c].get("type") == "Case"
                   and g.nodes[c].get("district") == "Mysuru")
    seed = max((n for n, d in g.nodes(data=True) if d.get("type") == "Suspect"),
               key=lambda s: (mysuru_firs(s), g.degree(s)))
    assert mysuru_firs(seed) >= 2
    _s, body = _get(client, f"/explore?seed={seed}&district=Mysuru", io)
    cases = [n for n in body["nodes"] if n["type"] == "Case"]
    assert cases and all(n["district"] == "Mysuru" for n in cases)
    # With a FIR filter set, nobody is reached through a registry associate alone.
    for n in body["nodes"]:
        if n["type"] == "Suspect" and n["id"] != seed:
            assert any(g.nodes[c].get("type") == "Case" and c in {x["id"] for x in cases}
                       for c in g[n["id"]]), n["id"]


def test_rowdy_filter_keeps_only_rowdy_sheeters(client, io):
    _s, body = _get(client, "/explore?seed=SUS-8286&rowdy=1", io)
    crim = main.data_cache["criminals"].set_index("criminal_id")
    for n in body["nodes"]:
        if n["type"] == "Suspect" and n["hop"] > 0:
            assert bool(crim.loc[n["id"], "rowdy_sheeter"]) and n["rowdy"]


def test_year_filter(client, io):
    _s, body = _get(client, "/explore?seed=SUS-8286&year_from=2024&year_to=2025", io)
    for n in body["nodes"]:
        if n["type"] == "Case":
            assert 2024 <= n["year"] <= 2025


def test_unknown_seed_and_syndicate_are_404(client, io):
    assert _get(client, "/explore?seed=SUS-NOPE", io)[0] == 404
    assert _get(client, "/explore?syndicate=SYN-NOPE", io)[0] == 404


def test_query_seeds_from_the_best_match(client, io):
    status, body = _get(client, "/explore?q=Kartik Bhartiya", io)
    assert status == 200 and body["focus"]["ids"] == ["SUS-1001"]
    status, body = _get(client, "/explore?q=zzzzqqqq", io)
    assert status == 200 and body["nodes"] == [] and "matches" in body["note"]


def test_syndicate_view_contains_every_member(client, io):
    syn = main.data_cache["syndicate_communities"][0]
    status, body = _get(client, f"/explore?syndicate={syn['syndicate_id']}", io)
    assert status == 200
    ids = {n["id"] for n in body["nodes"]}
    assert {m["id"] for m in syn["members"]} <= ids
    assert body["focus"]["kind"] == "syndicate"
    _assert_links_are_real(body)


def test_explore_audits_whose_network_was_read(client, io):
    _get(client, "/explore?seed=SUS-1001", io)
    rows = audit.recent(limit=50, subject_id="SUS-1001")
    assert any(r["action"] == "network_subjects" for r in rows)


def test_predicted_links_are_between_people_on_screen_and_not_already_linked(client, io):
    _s, body = _get(client, "/explore", io)
    ids = {n["id"] for n in body["nodes"] if n["type"] == "Suspect"}
    for p in body["predicted"]:
        assert p["source"] in ids and p["target"] in ids
        if not p["in_global_top"]:
            assert p["common_associates"] >= 2
            assert not G().has_edge(p["source"], p["target"])


# ------------------------------------------------------------------ path
def test_paths_walk_real_edges_and_the_first_is_shortest(client, io):
    status, body = _get(client, "/path?from=SUS-1001&to=SUS-8286&k=3", io)
    assert status == 200
    g = G()
    officers = {n for n, d in g.nodes(data=True) if d.get("type") == "Officer"}
    view = nx.subgraph_view(g, filter_node=lambda n: n not in officers)
    assert body["paths"], body["note"]
    assert len(body["paths"]) <= 3
    assert body["paths"][0]["length"] == nx.shortest_path_length(view, "SUS-1001", "SUS-8286")
    lengths = [p["length"] for p in body["paths"]]
    assert lengths == sorted(lengths)
    for p in body["paths"]:
        nodes = p["nodes"]
        assert nodes[0] == "SUS-1001" and nodes[-1] == "SUS-8286"
        assert len(set(nodes)) == len(nodes), "a path must be simple"
        assert not (set(nodes) & officers), "officers are excluded by default"
        assert len(p["hops"]) == len(nodes) - 1
        for a, b, hop in zip(nodes, nodes[1:], p["hops"]):
            assert g.has_edge(a, b)
            assert hop["relationship"] == g[a][b]["relationship"]
            assert hop["explanation"] and hop["from"] == a and hop["to"] == b
    ids = {n["id"] for n in body["graph"]["nodes"]}
    assert all(link["source"] in ids and link["target"] in ids for link in body["graph"]["links"])


def test_path_through_officers_only_when_asked(client, io):
    _s, body = _get(client, "/path?from=SUS-1001&to=SUS-8286&k=1&via_officers=1", io)
    _s, without = _get(client, "/path?from=SUS-1001&to=SUS-8286&k=1", io)
    assert body["paths"][0]["length"] <= without["paths"][0]["length"]
    assert body["search"]["via_officers"] is True


def test_path_validation(client, io):
    assert _get(client, "/path?from=SUS-1001&to=SUS-NOPE", io)[0] == 404
    assert _get(client, "/path?from=SUS-1001&to=SUS-1001", io)[0] == 400
    assert _get(client, "/path?from=SUS-1001", io)[0] == 400


def test_k_is_bounded(client, io):
    _s, body = _get(client, "/path?from=SUS-1001&to=SUS-8286&k=99", io)
    assert body["search"]["k"] == NI.PATH_K_MAX
    assert len(body["paths"]) <= NI.PATH_K_MAX


# ------------------------------------------------------------------ entity
def test_suspect_profile(client, io):
    status, body = _get(client, "/entity/SUS-1001", io)
    assert status == 200
    g = G()
    s = body["suspect"]
    crim = main.data_cache["criminals"].set_index("criminal_id").loc["SUS-1001"]
    assert s["name"] == crim["name"]
    assert s["flags"]["rowdy_sheeter"] == bool(crim["rowdy_sheeter"])
    assert s["flags"]["active_warrant"] == bool(crim["active_warrant"])
    assert s["cases_count"] == sum(1 for c in g["SUS-1001"] if g.nodes[c]["type"] == "Case")
    assert {link["href"] for link in body["links"]} == {"/suspect?id=SUS-1001",
                                                        "/map?track=SUS-1001"}


def test_recidivism_is_withheld_below_watchlist_names(client, io, sp):
    sid = next(iter(main.data_cache["recidivism"]["scores"]))
    _s, body = _get(client, f"/entity/{sid}", io)
    assert body["recidivism"]["withheld"] is True
    assert "index" not in body["recidivism"]
    _s, body = _get(client, f"/entity/{sid}", sp)
    assert body["recidivism"]["withheld"] is False
    assert body["recidivism"]["index"] == main.data_cache["recidivism"]["scores"][sid]


def test_case_profile_lists_the_accused_in_the_graph(client, io):
    g = G()
    case = next(n for n, d in g.nodes(data=True) if d.get("type") == "Case"
                and sum(1 for x in g[n] if g.nodes[x]["type"] == "Suspect") >= 2)
    _s, body = _get(client, f"/entity/{case}", io)
    accused = {a["id"] for a in body["case"]["accused"]}
    assert accused == {x for x in g[case] if g.nodes[x]["type"] == "Suspect"}
    assert body["links"][0]["href"] == f"/case?id={case}"


def test_vehicle_profile_omits_the_owner(client, io):
    plate = next(n for n, d in G().nodes(data=True) if d.get("type") == "Vehicle")
    _s, body = _get(client, f"/entity/{plate}", io)
    owner = main.data_cache["vehicles"].set_index("vehicle_number").loc[plate]
    text = str(body)
    assert str(owner["owner_name"]) not in text and str(owner["owner_phone"]) not in text


def test_officer_and_identifier_profiles(client, io):
    hub = _hub("Officer")
    _s, body = _get(client, f"/entity/{hub}", io)
    assert body["officer"]["cases_investigated"] == G().degree(hub)
    cyber = _hub("CyberID")
    _s, body = _get(client, f"/entity/{cyber}", io)
    assert body["identifier"]["cases_count"] == G().degree(cyber)


def test_unknown_entity_is_404(client, io):
    status, body = _get(client, "/entity/SUS-NOPE", io)
    assert status == 404 and body["error"] == "unknown_entity"


# ------------------------------------------------------------------ syndicates
def test_syndicates_list(client, io):
    status, body = _get(client, "/syndicates", io)
    assert status == 200
    assert body["total"] == len(main.data_cache["syndicate_communities"])
    first, src = body["syndicates"][0], main.data_cache["syndicate_communities"][0]
    assert first["syndicate_id"] == src["syndicate_id"]
    assert set(first["member_ids"]) == {m["id"] for m in src["members"]}
    assert first["size"] == len(first["members"]) and first["districts"] and first["crime_mix"]
    name = first["members"][0]["name"]
    _s, body = _get(client, f"/syndicates?q={name}", io)
    assert first["syndicate_id"] in [s["syndicate_id"] for s in body["syndicates"]]


def test_meta_reads_vocabularies_from_the_corpus(client, io):
    _s, body = _get(client, "/meta", io)
    assert len(body["districts"]) == main.data_cache["firs"]["district"].nunique()
    assert set(body["crime_types"]) == set(main.data_cache["firs"]["crime_type"].unique())


# ------------------------------------------------------------------ rebinding
def test_the_graph_is_read_through_the_getter_on_every_request(client, io):
    """main.py rebinds knowledge_graph on reload; a captured reference would keep
    answering about the previous corpus."""
    tiny = nx.Graph()
    tiny.add_node("SUS-T1", type="Suspect", name="Tiny Person", gang=None, risk_level="Low")
    tiny.add_node("CASE-T1", type="Case", crime_type="Extortion", district="Mysuru")
    tiny.add_edge("SUS-T1", "CASE-T1", relationship="Charged")
    try:
        NI.configure(lambda: tiny, lambda: {})
        _s, body = _get(client, "/search?q=Tiny Person", io)
        assert body["results"][0]["id"] == "SUS-T1"
        _s, body = _get(client, "/explore?seed=SUS-T1", io)
        assert {n["id"] for n in body["nodes"]} == {"SUS-T1", "CASE-T1"}
    finally:
        NI.configure(lambda: main.knowledge_graph, lambda: main.data_cache)
    _s, body = _get(client, "/search?q=Tiny Person", io)
    assert not body["results"] or body["results"][0]["id"] != "SUS-T1"
