"""SQLite store. The Case is the aggregate and is stored whole as JSON; notes live in their own table.

    python -m engine.store --load-fixtures     # load fixtures/golden_cases.json, checking every span
"""
import argparse
import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from engine.checks import check_case
from engine.config import AS_OF_DATE, DB_PATH, FIXTURES_PATH
from engine.models import Bucket, Case, CaseStatus, Note

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    patient_id   TEXT PRIMARY KEY,
    renewal_date TEXT NOT NULL,
    bucket       TEXT NOT NULL,
    fragile      INTEGER NOT NULL,
    status       TEXT NOT NULL,
    clinic_id    TEXT NOT NULL,
    data         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cases_renewal ON cases (renewal_date);
CREATE TABLE IF NOT EXISTS notes (
    id         TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    date       TEXT NOT NULL,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_patient ON notes (patient_id);
"""


def db_path() -> Path:
    return Path(os.environ.get("LAPSE_DB", DB_PATH))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.executescript(SCHEMA)
    return conn


def save_case(case: Case) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case.patient_id, case.renewal_date.isoformat(), case.bucket.value, int(case.fragile),
             case.status.value, case.clinic_id, case.model_dump_json()),
        )


def save_notes(notes: list[Note]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO notes VALUES (?, ?, ?, ?)",
            [(n.id, n.patient_id, n.date.isoformat(), n.model_dump_json()) for n in notes],
        )


def get_case(patient_id: str) -> Case | None:
    with connect() as conn:
        row = conn.execute("SELECT data FROM cases WHERE patient_id = ?", (patient_id,)).fetchone()
    return Case.model_validate_json(row[0]) if row else None


def get_notes(patient_id: str) -> list[Note]:
    with connect() as conn:
        rows = conn.execute("SELECT data FROM notes WHERE patient_id = ? ORDER BY date", (patient_id,)).fetchall()
    return [Note.model_validate_json(r[0]) for r in rows]


def list_cases(bucket: Bucket | str | None = None, fragile: bool | None = None,
               status: CaseStatus | str | None = None, clinic_id: str | None = None,
               window_days: int | None = None) -> list[Case]:
    """Cases matching every given filter, soonest renewal first. window_days counts from AS_OF_DATE."""
    where, args = [], []
    if bucket is not None:
        where.append("bucket = ?"); args.append(Bucket(bucket).value)
    if fragile is not None:
        where.append("fragile = ?"); args.append(int(fragile))
    if status is not None:
        where.append("status = ?"); args.append(CaseStatus(status).value)
    if clinic_id is not None:
        where.append("clinic_id = ?"); args.append(clinic_id)
    if window_days is not None:
        where.append("renewal_date BETWEEN ? AND ?")
        args += [AS_OF_DATE.isoformat(), (AS_OF_DATE + timedelta(days=window_days)).isoformat()]
    sql = "SELECT data FROM cases"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY renewal_date, patient_id"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [Case.model_validate_json(r[0]) for r in rows]


def read_fixtures(path: Path = FIXTURES_PATH) -> list[tuple[Case, list[Note]]]:
    """Parse and integrity-check the golden fixture file without touching the store."""
    out = []
    for entry in json.loads(Path(path).read_text()):
        notes = [Note.model_validate(n) for n in entry.pop("notes")]
        case = Case.model_validate(entry)
        check_case(case, notes)
        out.append((case, notes))
    return out


def load_fixtures(path: Path = FIXTURES_PATH) -> int:
    """(Re)load the golden cases, replacing any existing rows with the same ids. Returns the count."""
    loaded = read_fixtures(path)
    for case, notes in loaded:
        save_case(case)
        save_notes(notes)
    return len(loaded)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--load-fixtures", action="store_true", help="load fixtures/golden_cases.json")
    args = parser.parse_args()
    if args.load_fixtures:
        n = load_fixtures()
        claims = sum(len(c.claims) + len(c.dropped_claims) for c in list_cases())
        print(f"loaded {n} golden cases into {db_path()}; {claims} claim spans checked, 0 failures")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
