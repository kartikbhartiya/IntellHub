"""Load the CSV corpus into a real relational database.

Why this exists
---------------
The platform's whole claim about portability rests on one sentence: `ml/dataset.py`
is the only module that knows the physical schema, so pointing it at a different
source runs the identical pipeline. That claim was never tested. Every path still
ended at `pd.read_csv`.

This is the smallest thing that actually tests it. It builds a database from the
generated schema, loads the CSVs into it, and `ml.dataset.load_canonical(source=...)`
can then read from there instead — same canonical frames, same models, same metrics.

SQLite, not Postgres, on purpose: it needs no server, so the migration path can be
exercised in CI and on a laptop. The DDL in `schema.sql` is Postgres-flavoured and
SQLite accepts it, which is convenient rather than accidental — the types were chosen
to be portable. A Postgres deployment runs the same file.

What this does NOT do
---------------------
It does not make the application read from the database. `main.py` still loads the
CSV corpus into memory at boot, and the 512 MB ceiling and the missing write path are
unchanged. This proves the boundary holds and gives the migration something concrete
to build on; it is a foundation, not the finished job.

Run:  python load_to_db.py [--db corpus.db] [--verify]
"""
import argparse
import os
import sqlite3
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SCHEMA = os.path.join(HERE, "schema.sql")
DEFAULT_DB = os.path.join(HERE, "corpus.db")


def build(db_path, verbose=True):
    if not os.path.exists(SCHEMA):
        sys.exit("schema.sql not found — run `python generate_schema.py` first")

    if os.path.exists(db_path):
        os.remove(db_path)

    started = time.perf_counter()
    con = sqlite3.connect(db_path)
    con.executescript(open(SCHEMA, encoding="utf-8").read())

    loaded, skipped = 0, []
    # Foreign keys are enforced on read, not during the bulk load: the CSVs are
    # loaded table-by-table and a child would otherwise be rejected before its
    # parent exists. `--verify` turns enforcement on afterwards and checks.
    con.execute("PRAGMA foreign_keys=OFF")

    tables = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")]
    for table in tables:
        csv = os.path.join(DATA, table + ".csv")
        if not os.path.exists(csv):
            skipped.append(table)
            continue
        frame = pd.read_csv(csv)
        # Align to the declared column order; a column present in the CSV but not in
        # the schema is a schema bug, so it is reported rather than silently dropped.
        declared = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
        extra = [c for c in frame.columns if c not in declared]
        if extra and verbose:
            print(f"  ! {table}: columns absent from the schema: {extra}")
        frame = frame[[c for c in declared if c in frame.columns]]
        frame.to_sql(table, con, if_exists="append", index=False)
        loaded += len(frame)
        if verbose:
            print(f"  {table:<24} {len(frame):>7,} rows")

    con.commit()
    elapsed = time.perf_counter() - started
    size_mb = os.path.getsize(db_path) / 1e6
    if verbose:
        print(f"\nLoaded {loaded:,} rows into {db_path} "
              f"({size_mb:.1f} MB) in {elapsed:.1f}s")
        if skipped:
            print(f"No CSV for: {', '.join(skipped)}")
    con.close()
    return {"rows": loaded, "tables": len(tables) - len(skipped),
            "size_mb": round(size_mb, 1), "seconds": round(elapsed, 1)}


def verify(db_path):
    """Turn on referential integrity and see whether the corpus actually satisfies it.

    This is the check the CSVs could never be given. A dangling CaseMasterID in
    Accused is invisible in a flat file and an inner join silently drops the row —
    which is how a model ends up trained on fewer records than anyone believes.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    violations = con.execute("PRAGMA foreign_key_check").fetchall()
    counts = {}
    for table in ("CaseMaster", "Accused", "Victim", "ArrestSurrender",
                  "ChargesheetDetails", "CourtCasesRegistry"):
        try:
            counts[table] = con.execute(f'select count(*) from "{table}"').fetchone()[0]
        except sqlite3.Error:
            pass

    # The lookup the flat-file path does as a full scan on every request.
    started = time.perf_counter()
    row = con.execute(
        'select * from "CaseMaster" where "CrimeNo" = '
        '(select "CrimeNo" from "CaseMaster" limit 1)').fetchone()
    indexed_ms = (time.perf_counter() - started) * 1000
    con.close()

    print(f"Row counts: {counts}")
    print(f"Indexed lookup by CrimeNo: {indexed_ms:.2f} ms "
          f"({'hit' if row else 'no row'})")
    if violations:
        print(f"\n{len(violations)} referential integrity violation(s):")
        for table, rowid, parent, fkid in violations[:10]:
            print(f"  {table} row {rowid} -> missing parent in {parent}")
        return False
    print("\nReferential integrity: clean — every foreign key resolves.")
    return True


def main():
    ap = argparse.ArgumentParser(description="Load the CSV corpus into SQLite.")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--verify", action="store_true",
                    help="check referential integrity after loading")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    build(args.db, verbose=not args.quiet)
    if args.verify:
        print()
        if not verify(args.db):
            sys.exit(1)


if __name__ == "__main__":
    main()
