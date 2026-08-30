"""Generate a valid Postgres schema for the corpus.

Why this was rewritten
----------------------
The previous version guessed primary keys with a substring heuristic: any column
whose name contained "masterid" became `PRIMARY KEY`. Three tables therefore
declared several — `Accused` two, `Victim` two, `ArrestSurrender` three — which
Postgres rejects outright. The file it produced had never been executed, and
nothing in the codebase read it, so the error sat there undetected.

It also inferred types from `nrows=10`, so a column that is integer in its first
ten rows and null afterwards came out `BIGINT` and would fail on load.

What this does instead
----------------------
Keys are **declared**, not guessed. A schema is a set of assertions about the
data — that a case has one row, that an accused row points at a real case — and
those assertions have to be written down by someone who knows the domain, not
pattern-matched off column names.

Types are inferred from the whole file. Foreign keys and the indexes the query
paths actually need are emitted, because the point of moving off CSVs is to stop
doing full scans, and an unindexed table gives you the scans back with more
ceremony.

Run:  python generate_schema.py [--check]
      --check validates the existing schema.sql instead of rewriting it.
"""
import argparse
import os
import sys

import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

TYPES = {
    "int64": "BIGINT",
    "Int64": "BIGINT",
    "float64": "DOUBLE PRECISION",
    "object": "TEXT",
    "bool": "BOOLEAN",
    "datetime64[ns]": "TIMESTAMP",
}

# Declared, not guessed. `None` means the table has no natural single-column key —
# stated explicitly so that it reads as a decision rather than an omission.
PRIMARY_KEYS = {
    "State": "StateID", "District": "DistrictID", "UnitType": "UnitTypeID",
    "Unit": "UnitID", "Rank": "RankID", "Designation": "DesignationID",
    "Employee": "EmployeeID", "CaseCategory": "CaseCategoryID",
    "GravityOffence": "GravityOffenceID", "CaseStatusMaster": "CaseStatusID",
    "CasteMaster": "caste_master_id", "ReligionMaster": "ReligionID",
    "OccupationMaster": "OccupationID", "Court": "CourtID",
    "CrimeHead": "CrimeHeadID", "CrimeSubHead": "CrimeSubHeadID",
    "Act": "ActCode", "CaseMaster": "CaseMasterID",
    "ComplainantDetails": "ComplainantID", "Victim": "VictimMasterID",
    "Accused": "AccusedMasterID", "ArrestSurrender": "ArrestSurrenderID",
    "ChargesheetDetails": "CSID", "CriminalsRegistry": "PersonID",
    "OfficersRegistry": "EmployeeID", "CourtCasesRegistry": "CaseMasterID",
    "suspects": "suspect_id", "case_reports": "case_id",
    "stolen_vehicles": "vehicle_number", "cctv": "camera_id",
    "complaints": "complaint_id", "missing_persons": "person_id",
    "socioeconomic": "district", "cybercrime": "case_id",
    # Composite or key-less by nature:
    "Section": None,                 # (ActCode, SectionCode)
    "CrimeHeadActSection": None,     # (CrimeHeadID, ActCode, SectionCode)
    "ActSectionAssociation": None,   # many rows per case
    "case_links": None,              # (case_id, suspect_id) with a relationship
}

COMPOSITE_KEYS = {
    "Section": ["ActCode", "SectionCode"],
    "CrimeHeadActSection": ["CrimeHeadID", "ActCode", "SectionCode"],
}

# (table, column) -> (referenced table, referenced column)
FOREIGN_KEYS = {
    ("CaseMaster", "PoliceStationID"): ("Unit", "UnitID"),
    ("CaseMaster", "PolicePersonID"): ("Employee", "EmployeeID"),
    ("CaseMaster", "CaseStatusID"): ("CaseStatusMaster", "CaseStatusID"),
    ("CaseMaster", "CaseCategoryID"): ("CaseCategory", "CaseCategoryID"),
    ("CaseMaster", "GravityOffenceID"): ("GravityOffence", "GravityOffenceID"),
    ("CaseMaster", "CrimeMinorHeadID"): ("CrimeSubHead", "CrimeSubHeadID"),
    ("CaseMaster", "CourtID"): ("Court", "CourtID"),
    ("Accused", "CaseMasterID"): ("CaseMaster", "CaseMasterID"),
    ("Victim", "CaseMasterID"): ("CaseMaster", "CaseMasterID"),
    ("ComplainantDetails", "CaseMasterID"): ("CaseMaster", "CaseMasterID"),
    ("ActSectionAssociation", "CaseMasterID"): ("CaseMaster", "CaseMasterID"),
    ("ArrestSurrender", "CaseMasterID"): ("CaseMaster", "CaseMasterID"),
    ("ArrestSurrender", "AccusedMasterID"): ("Accused", "AccusedMasterID"),
    ("ChargesheetDetails", "CaseMasterID"): ("CaseMaster", "CaseMasterID"),
    ("CourtCasesRegistry", "CaseMasterID"): ("CaseMaster", "CaseMasterID"),
    ("OfficersRegistry", "EmployeeID"): ("Employee", "EmployeeID"),
    ("Employee", "UnitID"): ("Unit", "UnitID"),
    ("Employee", "RankID"): ("Rank", "RankID"),
    ("Unit", "DistrictID"): ("District", "DistrictID"),
    ("District", "StateID"): ("State", "StateID"),
    ("CrimeSubHead", "CrimeHeadID"): ("CrimeHead", "CrimeHeadID"),
}

# The lookups the application actually performs. Every one of these is a full
# boolean-mask scan today; they are the reason the API cannot paginate past 400 rows.
INDEXES = [
    ("CaseMaster", ["CrimeNo"]),
    ("CaseMaster", ["PoliceStationID"]),
    ("CaseMaster", ["CrimeRegisteredDate"]),
    ("CaseMaster", ["IncidentFromDate"]),
    ("CaseMaster", ["CrimeMinorHeadID"]),
    ("Accused", ["CaseMasterID"]),
    ("Accused", ["PersonID"]),
    ("Victim", ["CaseMasterID"]),
    ("ArrestSurrender", ["CaseMasterID"]),
    ("ArrestSurrender", ["AccusedMasterID"]),
    ("ChargesheetDetails", ["CaseMasterID"]),
    ("CourtCasesRegistry", ["conviction_status"]),
    ("case_links", ["suspect_id"]),
    ("case_links", ["case_id"]),
    ("case_reports", ["case_id"]),
]

DATE_HINTS = ("date", "_ts", "timestamp", "hearing")


def infer(series, column):
    """Type from the whole column, not the first ten rows.

    A column that is integer for ten rows and null afterwards came out BIGINT under
    the old `nrows=10` inference and would have failed on load.
    """
    if any(h in column.lower() for h in DATE_HINTS):
        parsed = pd.to_datetime(series, errors="coerce")
        if parsed.notna().mean() > 0.8:
            return "TIMESTAMP"
    dtype = str(series.dtype)
    if dtype == "float64" and series.dropna().apply(float.is_integer).all():
        return "BIGINT"          # integral values that pandas widened for the nulls
    return TYPES.get(dtype, "TEXT")


def _dependency_order(tables):
    """Emit referenced tables before the tables that reference them.

    Postgres resolves a REFERENCES clause at CREATE time, so alphabetical order
    fails the moment a table sorts ahead of its parent — `Accused` before
    `CaseMaster`, for instance. A plain topological sort fixes it; cycles (none
    here) fall back to source order rather than dropping a table.
    """
    deps = {t: set() for t in tables}
    for (child, _col), (parent, _ref) in FOREIGN_KEYS.items():
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)

    ordered, seen = [], set()

    def visit(table, trail):
        if table in seen or table in trail:
            return
        trail.add(table)
        for parent in sorted(deps.get(table, ())):
            visit(parent, trail)
        trail.discard(table)
        seen.add(table)
        ordered.append(table)

    for t in tables:
        visit(t, set())
    return ordered


def build():
    lines = [
        "-- KSP IntelHub — Postgres schema for the CCTNS-shaped corpus.",
        "-- Generated by generate_schema.py. Keys are declared in that file, not",
        "-- inferred: a schema is a set of assertions about the data and they have to",
        "-- be written down deliberately.",
        "",
    ]
    tables = _dependency_order(
        sorted(f[:-4] for f in os.listdir(DATA) if f.endswith(".csv")))

    for table in tables:
        df = pd.read_csv(os.path.join(DATA, table + ".csv"))
        pk = PRIMARY_KEYS.get(table, "MISSING")
        composite = COMPOSITE_KEYS.get(table)

        lines.append(f"-- {table}  ({len(df):,} rows)")
        lines.append(f'CREATE TABLE IF NOT EXISTS "{table}" (')
        cols = []
        for column in df.columns:
            sql_type = infer(df[column], column)
            suffix = ""
            if pk == column and not composite:
                suffix = " PRIMARY KEY"
            elif pk is None and not composite:
                suffix = ""
            cols.append(f'    "{column}" {sql_type}{suffix}')

        if composite:
            quoted = ", ".join(f'"{c}"' for c in composite)
            cols.append(f"    PRIMARY KEY ({quoted})")
        elif pk == "MISSING":
            lines.append(f"--   NOTE: no primary key declared for {table}; add one to")
            lines.append("--   PRIMARY_KEYS in generate_schema.py.")

        for (t, col), (ref_t, ref_c) in FOREIGN_KEYS.items():
            if t == table and col in df.columns:
                cols.append(
                    f'    FOREIGN KEY ("{col}") REFERENCES "{ref_t}" ("{ref_c}")')

        lines.append(",\n".join(cols))
        lines.append(");")
        lines.append("")

    lines.append("-- Indexes for the lookups the application actually performs.")
    lines.append("-- Every one of these is a full scan over an in-memory frame today.")
    for table, columns in INDEXES:
        name = f"idx_{table.lower()}_{'_'.join(c.lower() for c in columns)}"
        quoted = ", ".join(f'"{c}"' for c in columns)
        lines.append(
            f'CREATE INDEX IF NOT EXISTS {name} ON "{table}" ({quoted});')
    lines.append("")
    return "\n".join(lines)


def validate(sql):
    """Catch the class of error the old generator produced."""
    import re

    problems = []
    for m in re.finditer(r'CREATE TABLE IF NOT EXISTS "(\w+)" \((.*?)\n\);', sql, re.S):
        table, body = m.group(1), m.group(2)
        inline = len(re.findall(r"\bPRIMARY KEY\b", body))
        if inline > 1:
            problems.append(f"{table}: {inline} PRIMARY KEY declarations")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="validate the existing schema.sql instead of rewriting it")
    args = ap.parse_args()

    if args.check:
        if not os.path.exists(OUT):
            sys.exit("schema.sql does not exist")
        problems = validate(open(OUT, encoding="utf-8").read())
        if problems:
            sys.exit("invalid schema:\n  " + "\n  ".join(problems))
        print("schema.sql is valid")
        return

    sql = build()
    problems = validate(sql)
    if problems:
        sys.exit("refusing to write an invalid schema:\n  " + "\n  ".join(problems))
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sql)
    tables = sql.count("CREATE TABLE")
    print(f"Wrote {OUT}: {tables} tables, {sql.count('FOREIGN KEY')} foreign keys, "
          f"{sql.count('CREATE INDEX')} indexes")


if __name__ == "__main__":
    main()
