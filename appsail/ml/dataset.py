"""Adapter layer: raw tables on disk -> canonical dataframes the models consume.

This is the ONLY module that knows the physical schema. Every model downstream
speaks the canonical vocabulary defined below. To train on real KSP extracts,
point `load_canonical` at the new directory and adjust the mapping functions
here — no model, metric or endpoint changes.

Canonical frames
----------------
cases      one row per FIR         case_id, case_key, district, police_station,
                                   crime_type, ts, severity_score, status,
                                   latitude, longitude, weapon_used, vehicle_used,
                                   target_type, officer_id, witnesses, fatalities,
                                   injured, accused_count
reports    one row per narrative   case_id, report_text, officer_id
offenders  one row per person      offender_id, name, past_fir_count, past_arrests,
                                   rowdy_sheeter, history_sheet, active_warrant,
                                   gang_affiliation, risk_level, known_associates
accused    person <-> case         case_key, offender_id, age, gender
courts     one row per trial       case_key, chargesheet_status, conviction_status,
                                   court_name, next_hearing
officers   one row per officer     officer_id, rank, station, district,
                                   specialization, appointment_date, ...
arrests    one row per arrest      case_key, arrest_date, offender_id
socio      one row per district    district + indicator columns
links      case <-> suspect        case_id, suspect_id, relationship
"""
import os
import re

import pandas as pd

from . import config

_METADATA_PAT = re.compile(
    r"\[METADATA: SEV=(\d+);WPN=([^;]+);VEH=([^;]+);TGT=([^;]+);FAT=(\d+);INJ=(\d+);WIT=(\d+)\]"
)

_CORE_TABLES = [
    "State", "District", "UnitType", "Unit", "Rank", "Designation", "Employee",
    "CaseCategory", "GravityOffence", "CaseStatusMaster", "CasteMaster",
    "ReligionMaster", "OccupationMaster", "Court", "CrimeHead", "CrimeSubHead",
    "Act", "Section", "CrimeHeadActSection", "CaseMaster", "ComplainantDetails",
    "ActSectionAssociation", "Victim", "Accused", "ArrestSurrender",
    "ChargesheetDetails",
]

_REGISTRIES = {
    "CriminalsRegistry": "CriminalsRegistry.csv",
    "OfficersRegistry": "OfficersRegistry.csv",
    "CourtCasesRegistry": "CourtCasesRegistry.csv",
}

_OPTIONAL = {
    "vehicles": "stolen_vehicles.csv",
    "cctv": "cctv.csv",
    "complaints": "complaints.csv",
    "cybercrime": "cybercrime.csv",
    "missing": "missing_persons.csv",
    "socio": "socioeconomic.csv",
    "case_links": "case_links.csv",
    "case_reports": "case_reports.csv",
    "suspects": "suspects.csv",
}


def _time_of_day(ts_str):
    try:
        hour = int(str(ts_str).split()[1].split(":")[0])
    except Exception:
        return "Morning (7AM-10AM)"
    if hour >= 23 or hour < 5:
        return "Late Night (1AM-4AM)"
    if hour < 12:
        return "Morning (7AM-10AM)"
    if hour < 17:
        return "Afternoon (1PM-4PM)"
    return "Evening (5PM-8PM)"


def _read(data_dir, filename, required=True):
    path = os.path.join(data_dir, filename)
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError("Required table missing: " + path)
        return pd.DataFrame()
    return pd.read_csv(path)


def _read_sql(con, table, required=True):
    """One table out of a relational source, normalised to match the CSV path.

    SQL NULL comes back as `None` in an object column while `read_csv` produces
    `NaN`. Both are "missing" to `pd.isna`, but downstream code stringifies these
    values, and "none" versus "nan" is the kind of difference that gets absorbed by
    a magic tuple somewhere until one day it does not. Normalised here so the two
    sources are genuinely interchangeable rather than nearly so.
    """
    import numpy as np

    try:
        frame = pd.read_sql_query(f'SELECT * FROM "{table}"', con)
    except Exception as e:
        if required:
            raise RuntimeError(f"Required table missing from the database: {table}") from e
        return pd.DataFrame()

    for col in frame.columns:
        if frame[col].dtype == object:
            frame[col] = frame[col].where(frame[col].notna(), np.nan)
    return frame


def load_raw_from_db(db_path):
    """Read every physical table over SQL.

    The point of this function is that it is the *only* thing that changes when the
    source does. The canonical frames it feeds, and therefore every model, metric and
    endpoint downstream, are identical and unaware.

    SQLite here because it needs no server and can run in CI; the same query shapes
    work against Postgres through any DB-API connection, and `schema.sql` is written
    to load in both.
    """
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        raw = {}
        for t in _CORE_TABLES:
            raw[t] = _read_sql(con, t)
        for key, fname in _REGISTRIES.items():
            raw[key] = _read_sql(con, fname[:-4])
        for key, fname in _OPTIONAL.items():
            raw[key] = _read_sql(con, fname[:-4], required=False)
        return raw
    finally:
        con.close()


def load_raw(data_dir=None, source=None):
    """Read every physical table into a dict.

    `source` may be a path to a SQLite database; anything else falls back to the CSV
    directory. Optional tables degrade to empty frames either way.
    """
    if source and str(source).endswith((".db", ".sqlite", ".sqlite3")):
        return load_raw_from_db(source)

    data_dir = data_dir or config.DATA_DIR
    raw = {}
    for t in _CORE_TABLES:
        raw[t] = _read(data_dir, t + ".csv")
    for key, fname in _REGISTRIES.items():
        raw[key] = _read(data_dir, fname)
    for key, fname in _OPTIONAL.items():
        raw[key] = _read(data_dir, fname, required=False)
    return raw


def build_cases(raw):
    """CaseMaster + lookups + parsed narrative metadata -> canonical `cases`."""
    cm = raw["CaseMaster"]
    unit = raw["Unit"]
    unit_name = dict(zip(unit["UnitID"], unit["UnitName"]))
    unit_district = dict(zip(unit["UnitID"], unit["DistrictID"]))
    district_name = dict(zip(raw["District"]["DistrictID"], raw["District"]["DistrictName"]))
    subhead = dict(zip(raw["CrimeSubHead"]["CrimeSubHeadID"], raw["CrimeSubHead"]["CrimeHeadName"]))
    status = dict(zip(raw["CaseStatusMaster"]["CaseStatusID"], raw["CaseStatusMaster"]["CaseStatusName"]))
    emp_kgid = dict(zip(raw["Employee"]["EmployeeID"], raw["Employee"]["KGID"]))
    accused_counts = raw["Accused"].groupby("CaseMasterID").size().to_dict()

    meta = cm["BriefFacts"].astype(str).str.extract(_METADATA_PAT)
    meta.columns = ["severity_score", "weapon_used", "vehicle_used",
                    "target_type", "fatalities", "injured", "witnesses"]

    out = pd.DataFrame({
        "case_key": cm["CaseMasterID"],
        "case_id": cm["CrimeNo"].astype(str),
        "district": cm["PoliceStationID"].map(unit_district).map(district_name).fillna("Unknown"),
        "police_station": cm["PoliceStationID"].map(unit_name).fillna("Unknown PS"),
        "crime_type": cm["CrimeMinorHeadID"].map(subhead).fillna("Unknown"),
        "status": cm["CaseStatusID"].map(status).fillna("Under Investigation"),
        "officer_id": cm["PolicePersonID"].map(emp_kgid),
        "officer_key": cm["PolicePersonID"],
        "timestamp": cm["IncidentFromDate"],
        "latitude": pd.to_numeric(cm["latitude"], errors="coerce"),
        "longitude": pd.to_numeric(cm["longitude"], errors="coerce"),
        "gravity_id": cm["GravityOffenceID"],
        "crime_head_id": cm["CrimeMajorHeadID"],
    })
    out["registered_ts"] = pd.to_datetime(cm["CrimeRegisteredDate"], errors="coerce")
    out["severity_score"] = pd.to_numeric(meta["severity_score"], errors="coerce").fillna(50).astype(int)
    out["fatalities"] = pd.to_numeric(meta["fatalities"], errors="coerce").fillna(0).astype(int)
    out["injured"] = pd.to_numeric(meta["injured"], errors="coerce").fillna(0).astype(int)
    out["witnesses"] = pd.to_numeric(meta["witnesses"], errors="coerce").fillna(0).astype(int)
    out["weapon_used"] = meta["weapon_used"].fillna("None")
    out["vehicle_used"] = meta["vehicle_used"].fillna("None")
    out["target_type"] = meta["target_type"].fillna("Unknown")
    out["time_of_day"] = cm["IncidentFromDate"].map(_time_of_day)
    out["accused_count"] = out["case_key"].map(accused_counts).fillna(0).astype(int)
    out["ts"] = pd.to_datetime(out["timestamp"], errors="coerce")
    return out


def build_officers(raw):
    emp = raw["Employee"]
    unit = raw["Unit"]
    unit_name = dict(zip(unit["UnitID"], unit["UnitName"]))
    unit_district = dict(zip(unit["UnitID"], unit["DistrictID"]))
    district_name = dict(zip(raw["District"]["DistrictID"], raw["District"]["DistrictName"]))
    rank_name = dict(zip(raw["Rank"]["RankID"], raw["Rank"]["RankName"]))
    reg = raw["OfficersRegistry"]
    reg_idx = reg.set_index("EmployeeID") if not reg.empty else pd.DataFrame()

    def _reg(col, default):
        if reg_idx.empty or col not in reg_idx.columns:
            return pd.Series(default, index=emp.index)
        return emp["EmployeeID"].map(reg_idx[col]).fillna(default)

    return pd.DataFrame({
        "officer_id": emp["KGID"],
        "officer_key": emp["EmployeeID"],
        "name": emp["FirstName"],
        "rank": emp["RankID"].map(rank_name).fillna("PC"),
        "rank_id": emp["RankID"],
        "station": emp["UnitID"].map(unit_name).fillna("Unknown PS"),
        "district": emp["UnitID"].map(unit_district).map(district_name).fillna("Unknown"),
        "specialization": _reg("specialization", "General"),
        "cases_assigned": _reg("cases_assigned", 0),
        "closure_rate": _reg("closure_rate", 0.0),
        "commendations": _reg("commendations", 0),
        "appointment_date": pd.to_datetime(emp["AppointmentDate"], errors="coerce"),
        "dob": pd.to_datetime(emp["EmployeeDOB"], errors="coerce"),
        "gender": emp["GenderID"].map({1: "Male", 2: "Female"}).fillna("Male"),
    })


def build_offenders(raw):
    reg = raw["CriminalsRegistry"]
    if reg.empty:
        return pd.DataFrame()
    out = reg.rename(columns={"PersonID": "offender_id"}).copy()
    out["offender_id"] = out["offender_id"].astype(str)
    for col in ("rowdy_sheeter", "history_sheet", "active_warrant"):
        if col in out.columns:
            out[col] = out[col].astype(str).str.lower().isin(("true", "1", "yes"))
    for col in ("past_fir_count", "past_arrests"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
    if "known_associates" in out.columns:
        out["known_associates"] = out["known_associates"].fillna("")
    else:
        out["known_associates"] = ""
    if "gang_affiliation" in out.columns:
        out["gang_affiliation"] = out["gang_affiliation"].fillna("None")
    else:
        out["gang_affiliation"] = "None"
    return out


def build_accused(raw):
    acc = raw["Accused"]
    if acc.empty:
        return pd.DataFrame()
    return pd.DataFrame({
        "case_key": acc["CaseMasterID"],
        "offender_id": acc["PersonID"].astype(str),
        "name": acc["AccusedName"],
        "age": pd.to_numeric(acc["AgeYear"], errors="coerce"),
        "gender": pd.to_numeric(acc["GenderID"], errors="coerce"),
        "accused_key": acc["AccusedMasterID"],
    })


def build_courts(raw):
    cc = raw["CourtCasesRegistry"]
    if cc.empty:
        return pd.DataFrame()
    out = cc.rename(columns={"CaseMasterID": "case_key"}).copy()
    if "next_hearing" in out.columns:
        out["next_hearing"] = pd.to_datetime(out["next_hearing"], errors="coerce")
    return out


def build_arrests(raw):
    ar = raw["ArrestSurrender"]
    if ar.empty:
        return pd.DataFrame()
    acc = raw["Accused"]
    acc_map = dict(zip(acc["AccusedMasterID"], acc["PersonID"])) if not acc.empty else {}
    return pd.DataFrame({
        "case_key": ar["CaseMasterID"],
        "arrest_date": pd.to_datetime(ar["ArrestSurrenderDate"], errors="coerce"),
        "offender_id": ar["AccusedMasterID"].map(acc_map).astype(str),
        "station_id": ar["PoliceStationID"],
    })


def build_chargesheets(raw):
    cs = raw["ChargesheetDetails"]
    if cs.empty:
        return pd.DataFrame()
    return pd.DataFrame({
        "case_key": cs["CaseMasterID"],
        "chargesheet_date": pd.to_datetime(cs["csdate"], errors="coerce"),
        "chargesheet_type": cs["cstype"],
    })


def load_canonical(data_dir=None, source=None):
    """Full canonical bundle. The one function every training task calls.

    `source` accepts a SQLite path; the resulting frames are identical to the CSV
    path, which is the property that makes this module the swap point it claims to be.
    """
    raw = load_raw(data_dir, source=source)
    reports = raw["case_reports"].copy()
    if not reports.empty:
        reports["case_id"] = reports["case_id"].astype(str)
    links = raw["case_links"].copy()
    if not links.empty:
        links["case_id"] = links["case_id"].astype(str)
        links["suspect_id"] = links["suspect_id"].astype(str)

    return {
        "raw": raw,
        "cases": build_cases(raw),
        "reports": reports,
        "links": links,
        "suspects": raw["suspects"],
        "officers": build_officers(raw),
        "offenders": build_offenders(raw),
        "accused": build_accused(raw),
        "courts": build_courts(raw),
        "arrests": build_arrests(raw),
        "chargesheets": build_chargesheets(raw),
        "socio": raw["socio"],
    }


def describe(bundle):
    """Row/column census — recorded by the trainer so every run states what it saw."""
    out = {}
    for k, v in bundle.items():
        if isinstance(v, pd.DataFrame):
            out[k] = {"rows": int(len(v)), "cols": int(v.shape[1])}
    cases = bundle.get("cases")
    if cases is not None and not cases.empty:
        ts = cases["ts"].dropna()
        out["_span"] = {
            "first_incident": str(ts.min().date()) if len(ts) else None,
            "last_incident": str(ts.max().date()) if len(ts) else None,
            "crime_types": int(cases["crime_type"].nunique()),
            "districts": int(cases["district"].nunique()),
        }
    return out
