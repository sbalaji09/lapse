"""SQLite store. The Case is the aggregate and is stored whole as JSON; notes live in their own table.
Patients (Track A's raw cohort records, engine/cohort.py) are stored the same way for the later stages.

    python -m engine.store --load-fixtures     # load fixtures/golden_cases.json, checking every span
"""
import argparse
import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from engine.checks import check_case
from engine.cohort import Patient
from engine.config import AS_OF_DATE, DB_PATH, DEMO_ROSA_EMAIL, DEMO_ROSA_PHONE, FIXTURES_PATH
from engine.models import Bucket, Case, CaseStatus, Claim, Determination, Fact, Note, VoiceSession

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
CREATE TABLE IF NOT EXISTS patients (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id         TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    note_id    TEXT NOT NULL,
    verified   INTEGER,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_patient ON claims (patient_id);
CREATE TABLE IF NOT EXISTS baseline_cases (
    patient_id TEXT PRIMARY KEY,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_runs (
    patient_id TEXT NOT NULL,
    channel    TEXT NOT NULL,
    status     TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (patient_id, channel)
);
CREATE TABLE IF NOT EXISTS voice_sessions (
    id              TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    missing_fact_id TEXT NOT NULL,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS voice_sessions_case ON voice_sessions (case_id, created_at);
CREATE INDEX IF NOT EXISTS voice_sessions_status ON voice_sessions (status, created_at);
"""


def db_path() -> Path:
    return Path(os.environ.get("LAPSE_DB", DB_PATH))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.executescript(SCHEMA)
    return conn


def _apply_demo_contact_overrides(case: Case) -> Case:
    if case.patient_id == "g-rosa":
        case.email = os.environ.get("DEMO_INBOX") or DEMO_ROSA_EMAIL
        case.phone = os.environ.get("VOICE_DESTINATION_PHONE") or DEMO_ROSA_PHONE
    return case


def save_case(case: Case) -> None:
    case = _apply_demo_contact_overrides(case)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case.patient_id, case.renewal_date.isoformat(), case.bucket.value, int(case.fragile),
             case.status.value, case.clinic_id, case.model_dump_json()),
        )


def replace_cases(cases: list[Case]) -> None:
    """Swap in the pipeline's cases for the whole cohort in one transaction."""
    cases = [_apply_demo_contact_overrides(case) for case in cases]
    with connect() as conn:
        conn.execute("DELETE FROM cases")
        conn.execute("DELETE FROM voice_sessions")
        conn.executemany(
            "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(c.patient_id, c.renewal_date.isoformat(), c.bucket.value, int(c.fragile), c.status.value, c.clinic_id,
              c.model_dump_json()) for c in cases],
        )


def save_baseline(cases: list[Case]) -> None:
    """Remember these cases' pre-demo state; reset_demo() puts them back."""
    cases = [_apply_demo_contact_overrides(case) for case in cases]
    with connect() as conn:
        conn.execute("DELETE FROM baseline_cases")
        conn.executemany("INSERT INTO baseline_cases VALUES (?, ?)", [(c.patient_id, c.model_dump_json()) for c in cases])


def reset_demo() -> int:
    """Restore the demo cases to their pre-demo state: the pipeline's own output when a pipeline has run (so every
    number on screen matches the eval), else the hand-built fixtures. Returns how many cases were restored."""
    with connect() as conn:
        rows = conn.execute("SELECT data FROM baseline_cases").fetchall()
        conn.execute("DELETE FROM voice_sessions")
    if not rows:
        return load_fixtures()
    for (raw,) in rows:
        save_case(Case.model_validate_json(raw))
    return len(rows)


def save_notes(notes: list[Note]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO notes VALUES (?, ?, ?, ?)",
            [(n.id, n.patient_id, n.date.isoformat(), n.model_dump_json()) for n in notes],
        )


def get_case(patient_id: str) -> Case | None:
    with connect() as conn:
        row = conn.execute("SELECT data FROM cases WHERE patient_id = ?", (patient_id,)).fetchone()
    return _apply_demo_contact_overrides(Case.model_validate_json(row[0])) if row else None


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
    return [_apply_demo_contact_overrides(Case.model_validate_json(r[0])) for r in rows]


def replace_cohort(patients: list[Patient], notes: list[Note]) -> None:
    """Swap in a freshly generated cohort and all of its notes in one transaction. Cases are left alone."""
    with connect() as conn:
        conn.execute("DELETE FROM patients")
        conn.execute("DELETE FROM notes")
        conn.executemany("INSERT INTO patients VALUES (?, ?)", [(p.id, p.model_dump_json()) for p in patients])
        conn.executemany("INSERT INTO notes VALUES (?, ?, ?, ?)",
                         [(n.id, n.patient_id, n.date.isoformat(), n.model_dump_json()) for n in notes])


def all_notes() -> list[Note]:
    with connect() as conn:
        rows = conn.execute("SELECT data FROM notes ORDER BY patient_id, date, id").fetchall()
    return [Note.model_validate_json(r[0]) for r in rows]


def replace_claims(patient_ids: set[str], claims: list[Claim]) -> None:
    """Swap in Channel B's claims (verified and dropped) for these patients in one transaction."""
    with connect() as conn:
        conn.executemany("DELETE FROM claims WHERE patient_id = ?", [(pid,) for pid in patient_ids])
        conn.executemany("INSERT INTO claims VALUES (?, ?, ?, ?, ?)",
                         [(c.id, c.patient_id, c.note_id, None if c.verified is None else int(c.verified),
                           c.model_dump_json()) for c in claims])


def list_claims(patient_id: str | None = None) -> list[Claim]:
    sql, args = "SELECT data FROM claims", ()
    if patient_id is not None:
        sql, args = sql + " WHERE patient_id = ?", (patient_id,)
    with connect() as conn:
        rows = conn.execute(sql + " ORDER BY id", args).fetchall()
    return [Claim.model_validate_json(r[0]) for r in rows]


def get_patient(patient_id: str) -> Patient | None:
    with connect() as conn:
        row = conn.execute("SELECT data FROM patients WHERE id = ?", (patient_id,)).fetchone()
    return Patient.model_validate_json(row[0]) if row else None


def list_patients() -> list[Patient]:
    with connect() as conn:
        rows = conn.execute("SELECT data FROM patients ORDER BY id").fetchall()
    return [Patient.model_validate_json(r[0]) for r in rows]


def save_channel_runs(channel: str, runs: list[tuple[Determination, list[Fact]]], replace_all: bool = True) -> None:
    """Store one channel's results ("A", "B", ...) in one transaction; by default replacing all earlier ones."""
    with connect() as conn:
        if replace_all:
            conn.execute("DELETE FROM channel_runs WHERE channel = ?", (channel,))
        else:
            conn.executemany("DELETE FROM channel_runs WHERE channel = ? AND patient_id = ?",
                             [(channel, d.patient_id) for d, _ in runs])
        conn.executemany(
            "INSERT INTO channel_runs VALUES (?, ?, ?, ?)",
            [(d.patient_id, channel, d.status,
              json.dumps({"determination": d.model_dump(mode="json"), "facts": [f.model_dump(mode="json") for f in fs]}))
             for d, fs in runs],
        )


def get_channel_run(patient_id: str, channel: str) -> tuple[Determination, list[Fact]] | None:
    with connect() as conn:
        row = conn.execute("SELECT data FROM channel_runs WHERE patient_id = ? AND channel = ?",
                           (patient_id, channel)).fetchone()
    if not row:
        return None
    data = json.loads(row[0])
    return Determination.model_validate(data["determination"]), [Fact.model_validate(f) for f in data["facts"]]


def list_channel_runs(channel: str) -> dict[str, tuple[Determination, list[Fact]]]:
    with connect() as conn:
        rows = conn.execute("SELECT data FROM channel_runs WHERE channel = ? ORDER BY patient_id", (channel,)).fetchall()
    out = {}
    for (raw,) in rows:
        data = json.loads(raw)
        d = Determination.model_validate(data["determination"])
        out[d.patient_id] = (d, [Fact.model_validate(f) for f in data["facts"]])
    return out


def save_voice_session(session: VoiceSession) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO voice_sessions VALUES (?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.case_id,
                session.missing_fact_id,
                session.status.value,
                session.created_at.isoformat(),
                session.model_dump_json(),
            ),
        )


def create_voice_session(session: VoiceSession) -> bool:
    """Claim a new voice attempt. The primary key prevents duplicate schedulers from dialing twice."""
    with connect() as conn:
        result = conn.execute(
            "INSERT OR IGNORE INTO voice_sessions VALUES (?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.case_id,
                session.missing_fact_id,
                session.status.value,
                session.created_at.isoformat(),
                session.model_dump_json(),
            ),
        )
    return result.rowcount == 1


def get_voice_session(session_id: str) -> VoiceSession | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT data FROM voice_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return VoiceSession.model_validate_json(row[0]) if row else None


def list_voice_sessions(
    case_id: str | None = None,
    status: str | None = None,
) -> list[VoiceSession]:
    where: list[str] = []
    args: list[str] = []
    if case_id is not None:
        where.append("case_id = ?")
        args.append(case_id)
    if status is not None:
        where.append("status = ?")
        args.append(status)
    sql = "SELECT data FROM voice_sessions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at, id"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [VoiceSession.model_validate_json(row[0]) for row in rows]


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
