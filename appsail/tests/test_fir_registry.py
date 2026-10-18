"""FIR registration: schema-true rows, correct numbering, refusals with reasons.

* **Numbering is the corpus's.** CrimeNo = category, district (4), station (4), year,
  serial (5); the serial continues from the highest already issued for that
  (category, district, station, year), and a second registration takes the next one.
* **METADATA round-trips.** BriefFacts must end in the block main.py parses, or the
  FIR frame silently falls back to severity 50 and no weapon.
* **Every code is a master code.** An unknown station, section or occupation is a
  400 naming the field, never a row the rest of the platform cannot resolve.
* **Jurisdiction.** A station officer registers at their own station only.
"""
import os
import re
import sys
from datetime import datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import fir_registry as F  # noqa: E402

META = re.compile(r"\[METADATA: SEV=(\d+);WPN=([^;]+);VEH=([^;]+);TGT=([^;]+);FAT=(\d+);INJ=(\d+);WIT=(\d+)\]$")
NOW = datetime(2026, 10, 18, 12, 0, 0)


@pytest.fixture
def cache():
    main.load_and_build_intelligence()
    # A private copy so registrations here never leak into other test modules.
    c = {k: (v.copy() if isinstance(v, pd.DataFrame) else v) for k, v in list(main.data_cache.items())}
    seen = []
    F.configure(lambda: c, on_registered=seen.append)
    c["_seen"] = seen
    if os.path.exists(F.STORE_PATH):
        os.remove(F.STORE_PATH)
    return c


def _station(c, name="Koramangala PS"):
    u = c["Unit"]
    return int(u.loc[u["UnitName"] == name, "UnitID"].iloc[0])


def _payload(c, **over):
    p = {
        "station_id": _station(c), "case_category_id": 1, "gravity_id": 2, "crime_type_id": 1,
        "sections": [{"act": "IPC", "section": "379"}, {"act": "IPC", "section": "34"}],
        "officer_id": int(c["Employee"]["EmployeeID"].iloc[0]),
        "incident_from": "2026-10-17T20:15", "incident_to": "2026-10-17T20:20", "info_received": "2026-10-17T21:05",
        "place_of_occurrence": "80 Feet Road, near Sony signal",
        "brief_facts": "The complainant was walking home when two persons on a motorcycle snatched her gold chain.",
        "weapon": "None", "vehicle": "Bajaj Pulsar", "target": "Public Area",
        "fatalities": 0, "injured": 1, "witnesses": 2,
        "complainant": {"name": "Lakshmi R", "age": 54, "gender_id": 2, "occupation_id": 1, "religion_id": 1, "caste_id": 1},
        "victims": [{"name": "Lakshmi R", "age": 54, "gender_id": 2}],
        "accused": [{"person_id": "SUS-1001", "age": 30, "gender_id": 1}, {"name": "Unknown rider", "gender_id": 1}],
    }
    p.update(over)
    return p


def test_a_registration_writes_schema_true_rows(cache):
    out = F.register(_payload(cache), now=NOW)
    cm = cache["CaseMaster"].iloc[-1]
    st = _station(cache)
    d = int(cache["Unit"].set_index("UnitID").loc[st, "DistrictID"])
    assert out["crime_no"] == cm["CrimeNo"] and cm["CrimeNo"].startswith(f"1{d:04d}{st:04d}2026")
    assert len(cm["CrimeNo"]) == 18 and cm["CaseNo"] == "2026" + cm["CrimeNo"][-5:]
    assert cm["CaseStatusID"] == 1 and cm["CrimeRegisteredDate"] == "2026-10-18"
    m = META.search(cm["BriefFacts"])
    assert m and m.group(2) == "None" and m.group(3) == "Bajaj Pulsar" and m.group(6) == "1"
    acc = cache["Accused"][cache["Accused"]["CaseMasterID"] == cm["CaseMasterID"]]
    assert list(acc["PersonID"]) == ["SUS-1001", ""]
    assert acc["AccusedName"].iloc[0] == main.data_cache["criminals"].set_index("criminal_id").loc["SUS-1001", "name"]
    asa = cache["ActSectionAssociation"][cache["ActSectionAssociation"]["CaseMasterID"] == cm["CaseMasterID"]]
    assert list(asa["SectionID"]) == ["379", "34"] and list(asa["SectionOrderID"]) == [1, 2]
    bundle = cache["_seen"][-1]
    assert bundle["links"] == [{"case_id": out["crime_no"], "suspect_id": "SUS-1001", "relationship": "Charged"}]
    assert os.path.exists(F.STORE_PATH) and F.stored_bundles()[-1]["crime_no"] == out["crime_no"]


def test_the_serial_continues_from_the_highest_issued(cache):
    st = _station(cache)
    d = int(cache["Unit"].set_index("UnitID").loc[st, "DistrictID"])
    prefix = f"1{d:04d}{st:04d}2026"
    nos = cache["CaseMaster"]["CrimeNo"].astype(str)
    prior = nos[nos.str.startswith(prefix)].str[-5:].astype(int).max() if nos.str.startswith(prefix).any() else 0
    a = F.register(_payload(cache), now=NOW)["crime_no"]
    b = F.register(_payload(cache), now=NOW)["crime_no"]
    assert int(a[-5:]) == prior + 1 and int(b[-5:]) == prior + 2
    assert cache["CaseMaster"]["CaseMasterID"].is_unique and cache["Victim"]["VictimMasterID"].is_unique


@pytest.mark.parametrize("over,field", [
    ({"station_id": 999999}, "station_id"),
    ({"sections": [{"act": "IPC", "section": "999"}]}, "sections.0"),
    ({"sections": []}, "sections"),
    ({"crime_type_id": 77}, "crime_type_id"),
    ({"incident_from": "2027-01-01T10:00"}, "incident_from"),
    ({"info_received": "2026-10-16T10:00"}, "info_received"),
    ({"latitude": 28.6, "longitude": 77.2}, "latitude"),
    ({"brief_facts": "too short"}, "brief_facts"),
    ({"brief_facts": "x" * 40 + " [METADATA: SEV=99]"}, "brief_facts"),
    ({"weapon": "Bazooka"}, "weapon"),
    ({"victims": []}, "victims"),
    ({"accused": [{"person_id": "SUS-0000", "gender_id": 1}]}, "accused.0.person_id"),
])
def test_bad_input_names_the_field(cache, over, field):
    p = _payload(cache, **over)
    if "complainant" in over:
        pass
    with pytest.raises(F.FIRError) as ex:
        F.register(p, now=NOW)
    assert field in ex.value.errors, ex.value.errors
    assert not cache["_seen"]


def test_a_bad_complainant_code_is_refused(cache):
    p = _payload(cache)
    p["complainant"]["occupation_id"] = 999
    with pytest.raises(F.FIRError) as ex:
        F.register(p, now=NOW)
    assert "complainant.occupation_id" in ex.value.errors


def _ident(level, district=None, station=None, perms=("register_fir",)):
    return {"user": "t", "permissions": list(perms), "scope": {"level": level, "district": district, "station": station}}


def test_jurisdiction(cache):
    p = _payload(cache)
    F.register(p, _ident("station", "Bengaluru Urban", "Koramangala PS"), now=NOW)
    with pytest.raises(PermissionError):
        F.register(p, _ident("station", "Bengaluru Urban", "Indiranagar PS"), now=NOW)
    with pytest.raises(PermissionError):
        F.register(p, _ident("district", "Mysuru"), now=NOW)
    with pytest.raises(PermissionError):
        F.register(p, _ident("state", perms=()), now=NOW)
    F.register(p, _ident("district", "Bengaluru Urban"), now=NOW)


def test_masters_cover_every_lookup(cache):
    m = F.masters()
    assert len(m["districts"]) == 31 and len(m["stations"]) == len(cache["Unit"])
    assert {s["act"] for s in m["sections"]} >= {"IPC", "BNS", "NDPS", "IT Act"}
    assert all(t["suggested_section"] for t in m["crime_types"])
    names = {(s["act"], s["section"]) for s in m["sections"]}
    assert all((t["suggested_section"]["act"], t["suggested_section"]["section"]) in names for t in m["crime_types"])
