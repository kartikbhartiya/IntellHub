"""Demographics and case-flow aggregates: the numbers add up, and nothing personal leaks.

What these pin down, and why each one matters for a chart:

* **Conservation.** A Sankey whose middle node takes in more than it lets out, or
  whose columns sum to different totals, draws ribbons that appear from nowhere.
  Every status node's inflow must equal its outflow, and every column must sum to
  the number of FIRs in scope.
* **The totals are the corpus's.** Each count is recomputed here independently with
  pandas from the cached tables, so a join that drops or duplicates rows fails.
* **Shares sum to one.** Rounded shares that add to 100.1% are the first thing a
  reader notices on a donut.
* **Open, and aggregate-only.** Both endpoints answer without a token, and no name,
  person id, case number, caste or religion appears anywhere in a response.
"""
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import insights  # noqa: E402

DEMO = "/serverless/api/analytics/demographics"
FLOW = "/serverless/api/lifecycle/flow"


@pytest.fixture(scope="module", autouse=True)
def loaded():
    if "insights" not in main.app.blueprints:
        main.app.register_blueprint(insights.bp)
    insights.configure(lambda: main.data_cache)
    main.load_and_build_intelligence()


@pytest.fixture(scope="module")
def client():
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def _get(client, url):
    r = client.get(url)
    return r.status_code, r.get_json(), r.get_data(as_text=True)


def C(key):
    return main.data_cache[key]


def _firs(district=None, crime_type=None):
    f = C("firs")
    if district:
        f = f[f["district"] == district]
    if crime_type:
        f = f[f["crime_type"] == crime_type]
    return f


def _busiest_district():
    return C("firs")["district"].value_counts().index[0]


# ------------------------------------------------------------------ open access
@pytest.mark.parametrize("url", [DEMO, FLOW, f"{FLOW}?group=head"])
def test_endpoints_answer_without_a_token(client, url):
    status, body, _ = _get(client, url)
    assert status == 200, body
    assert body["filters"]


# ----------------------------------------------------------------- demographics
def _pandas_occupation(district=None, crime_type=None):
    ids = set(_firs(district, crime_type)["CaseMasterID"])
    cd = C("ComplainantDetails")
    cd = cd[cd["CaseMasterID"].isin(ids)]
    names = dict(zip(C("OccupationMaster")["OccupationID"], C("OccupationMaster")["OccupationName"]))
    return len(cd), cd["OccupationID"].map(names).value_counts().to_dict()


def test_statewide_occupation_counts_match_pandas(client):
    _, body, _ = _get(client, DEMO)
    n, expected = _pandas_occupation()
    occ = body["occupation"]
    assert body["firs"] == len(C("firs"))
    assert occ["n"] == n
    got = {c["name"]: c["count"] for c in occ["categories"]}
    assert got == expected
    assert sum(got.values()) == n
    # Ranked, largest first, and the top is the largest.
    counts = [c["count"] for c in occ["categories"]]
    assert counts == sorted(counts, reverse=True)
    assert occ["top"]["count"] == max(counts)


@pytest.mark.parametrize("qs", ["", "?district=Mysuru", "?crime_type=Murder",
                                "?district=Mysuru&crime_type=Murder"])
def test_every_distribution_sums_to_one(client, qs):
    _, body, _ = _get(client, DEMO + qs)
    assert not body["withheld"]
    dists = [body["occupation"]["categories"]]
    for who in ("complainant", "victim", "accused"):
        dists += [body[who]["age_bands"], body[who]["gender"]]
    for d in dists:
        assert sum(x["share"] for x in d) == pytest.approx(1.0, abs=1e-9)
        assert all(0 <= x["share"] <= 1 for x in d)


def test_filters_are_applied_and_canonicalised(client):
    d = _busiest_district()
    ct = C("firs")["crime_type"].value_counts().index[-1]
    status, body, _ = _get(client, f"{DEMO}?district={d.lower()}&crime_type={ct.upper()}")
    assert status == 200
    assert body["filters"] == {"district": d, "crime_type": ct}
    assert body["firs"] == len(_firs(d, ct))
    n, expected = _pandas_occupation(d, ct)
    assert body["occupation"]["n"] == n
    assert {c["name"]: c["count"] for c in body["occupation"]["categories"] if c["count"]} == expected

    ids = set(_firs(d, ct)["CaseMasterID"])
    victims = C("Victim")[C("Victim")["CaseMasterID"].isin(ids)]
    accused = C("Accused")[C("Accused")["CaseMasterID"].isin(ids)]
    assert body["victim"]["n"] == len(victims)
    assert body["accused"]["n"] == len(accused)
    assert body["accused"]["distinct_persons"] == accused["PersonID"].nunique()
    male = int((victims["GenderID"] == 1).sum())
    assert next(g for g in body["victim"]["gender"] if g["name"] == "Male")["count"] == male


def test_age_bands_cover_every_person(client):
    _, body, _ = _get(client, DEMO)
    for who, table in (("complainant", "ComplainantDetails"), ("victim", "Victim"),
                       ("accused", "Accused")):
        bands = body[who]["age_bands"]
        assert sum(b["count"] for b in bands) == body[who]["n"]
        ages = C(table)[C(table)["CaseMasterID"].isin(C("firs")["CaseMasterID"])]["AgeYear"]
        assert next(b for b in bands if b["band"] == "30–44")["count"] == int(ages.between(30, 44).sum())


def test_unknown_filters_are_refused_not_answered_with_zeros(client):
    status, body, _ = _get(client, f"{DEMO}?district=Atlantis")
    assert status == 400 and "Atlantis" in body["error"]
    assert _busiest_district() in body["options"]
    status, _, _ = _get(client, f"{DEMO}?crime_type=Piracy")
    assert status == 400


def test_near_uniform_occupation_is_called_out(client):
    _, body, _ = _get(client, DEMO)
    u = body["occupation"]["uniformity"]
    assert u["testable"] and u["df"] == len(C("OccupationMaster")) - 1
    # The simulated corpus draws occupation evenly; the note must say so rather than
    # let a ranking of noise read as a finding.
    if u["near_uniform"]:
        assert "even split" in body["note"] and "simulated" in body["note"]
    assert body["note"]


def test_small_scopes_are_withheld(monkeypatch):
    monkeypatch.setattr(insights, "MIN_SCOPE_FIRS", 10 ** 9)
    body = insights.build_demographics("Mysuru", None)
    assert body["withheld"] is True
    assert body["occupation"] is None and body["victim"] is None and body["accused"] is None
    assert body["firs"] == len(_firs("Mysuru"))


# -------------------------------------------------------------------- case flow
def _check_conservation(body):
    n = body["totals"]["firs"]
    nodes = {x["id"]: x for x in body["nodes"]}
    assert {x["column"] for x in body["nodes"]} <= {0, 1, 2}
    for col in (0, 1, 2):
        assert sum(x["value"] for x in body["nodes"] if x["column"] == col) == n
    assert body["totals"]["columns"] == [n, n, n]

    inflow, outflow = {}, {}
    for link in body["links"]:
        assert link["value"] > 0
        assert nodes[link["source"]]["column"] + 1 == nodes[link["target"]]["column"]
        outflow[link["source"]] = outflow.get(link["source"], 0) + link["value"]
        inflow[link["target"]] = inflow.get(link["target"], 0) + link["value"]
    for nid, node in nodes.items():
        if node["column"] == 0:
            assert outflow[nid] == node["value"]
        elif node["column"] == 1:
            assert inflow[nid] == node["value"] == outflow[nid]
        else:
            assert inflow[nid] == node["value"]

    # The three-way paths are the same flow at finer grain.
    assert sum(p["value"] for p in body["paths"]) == n
    agg = {}
    for p in body["paths"]:
        agg[(p["source"], p["via"])] = agg.get((p["source"], p["via"]), 0) + p["value"]
        agg[(p["via"], p["target"])] = agg.get((p["via"], p["target"]), 0) + p["value"]
    assert agg == {(lk["source"], lk["target"]): lk["value"] for lk in body["links"]}


@pytest.mark.parametrize("qs", ["", "?group=head", "?district=Mysuru",
                                "?district=Bengaluru%20Urban&group=head"])
def test_flow_conserves(client, qs):
    status, body, _ = _get(client, FLOW + qs)
    assert status == 200
    _check_conservation(body)


def _pandas_outcomes(district=None):
    f = _firs(district)
    verdict = C("courts").drop_duplicates("fir_number", keep="last").set_index(
        "fir_number")["conviction_status"]
    v = f["case_id"].astype(str).map(verdict)
    cs = f["status"] == "Charge Sheeted"
    out = {
        "Still under investigation": int((f["status"] == "Under Investigation").sum()),
        "Closed": int((f["status"] == "Closed").sum()),
        "Awaiting court record": int((cs & v.isna()).sum()),
    }
    for verdict_name in ("Convicted", "Acquitted", "Pending Trial"):
        out[verdict_name] = int((cs & (v == verdict_name)).sum())
    return {k: c for k, c in out.items() if c}, int((~cs & v.notna()).sum())


@pytest.mark.parametrize("district", [None, "Mysuru"])
def test_flow_totals_match_pandas(client, district):
    url = FLOW + (f"?district={district}" if district else "")
    _, body, _ = _get(client, url)
    f = _firs(district)
    assert body["totals"]["firs"] == len(f)
    assert body["totals"]["by_status"] == f["status"].value_counts().to_dict()
    outcomes, ignored = _pandas_outcomes(district)
    assert body["totals"]["by_outcome"] == outcomes
    assert body["totals"]["court_records_ignored"] == ignored
    crime = {x["label"]: x["value"] for x in body["nodes"] if x["column"] == 0}
    assert crime == f["crime_type"].value_counts().to_dict()
    # A court record on a never-charge-sheeted FIR is reported, not silently used.
    if ignored:
        assert f"{ignored:,}" in body["note"]


def test_head_groups_follow_crime_subhead(client):
    _, body, _ = _get(client, f"{FLOW}?group=head")
    sub = C("CrimeSubHead")
    head = dict(zip(C("CrimeHead")["CrimeHeadID"], C("CrimeHead")["CrimeGroupName"]))
    minor = dict(zip(C("CaseMaster")["CaseMasterID"], C("CaseMaster")["CrimeMinorHeadID"]))
    to_head = dict(zip(sub["CrimeSubHeadID"], sub["CrimeHeadID"]))
    groups = C("firs")["CaseMasterID"].map(minor).map(to_head).map(head)
    expected = groups.value_counts().to_dict()
    got = {x["label"]: x["value"] for x in body["nodes"] if x["column"] == 0}
    assert got == expected
    assert set(got) <= set(head.values())
    assert body["columns"][0] == "Crime-head group"
    # Column 0 follows the CrimeHead master order, not size, so colours stay put.
    order = [x["label"] for x in body["nodes"] if x["column"] == 0]
    assert order == [h for _, h in sorted(head.items()) if h in got]


def test_crime_types_carry_their_head_group(client):
    _, body, _ = _get(client, FLOW)
    sub = C("CrimeSubHead")
    head = dict(zip(C("CrimeHead")["CrimeHeadID"], C("CrimeHead")["CrimeGroupName"]))
    want = {n: head[h] for n, h in zip(sub["CrimeHeadName"], sub["CrimeHeadID"])}
    for x in body["nodes"]:
        if x["column"] == 0:
            assert x["group"] == want[x["label"]]
            assert 0 <= x["shade"] < x["shades"]


def test_flow_district_filter_and_bad_parameters(client):
    d = _busiest_district()
    status, body, _ = _get(client, f"{FLOW}?district={d.upper()}")
    assert status == 200 and body["filters"]["district"] == d
    assert body["totals"]["firs"] == len(_firs(d))
    assert _get(client, f"{FLOW}?district=Atlantis")[0] == 400
    assert _get(client, f"{FLOW}?group=station")[0] == 400


# ----------------------------------------------------------- nothing personal
def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


@pytest.mark.parametrize("url", [DEMO, f"{DEMO}?district=Mysuru&crime_type=Murder",
                                 FLOW, f"{FLOW}?district=Mysuru&group=head"])
def test_no_names_ids_caste_or_religion_in_responses(client, url):
    _, body, text = _get(client, url)
    names = set()
    for table, col in (("ComplainantDetails", "ComplainantName"), ("Victim", "VictimName"),
                       ("Accused", "AccusedName")):
        names |= set(C(table)[col].dropna().astype(str))
    decoded = json.dumps(body, ensure_ascii=False)
    leaked = [n for n in names if len(n) > 3 and n in decoded]
    assert not leaked, leaked[:5]
    assert "SUS-" not in decoded
    sample_case = str(C("firs")["case_id"].iloc[0])
    assert sample_case not in decoded

    keys = {k.lower() for k in _keys(body)}
    assert not any("caste" in k or "religion" in k for k in keys)
    lowered = decoded.lower()
    for word in ("lingayat", "vokkaliga", "kuruba", "dalit", "hindu", "muslim",
                 "christian", "sikh", "jain"):
        assert word not in lowered, word
