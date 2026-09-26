"""Mock external databases (data/external/*.json), keyed by patient_id and generated from the truth.

Names match the `database:` values in rules/fact_registry.yaml. The state simulator may read only the ones
listed in the rule pack's state_visible_sources; everything else is a lookup Lapse can make on the
clinic's behalf ("bother nobody") before asking a person.

A missing key means "no record": the person is not in that system.
"""
import json
from functools import cache

from engine.config import AS_OF_DATE, EXTERNAL_DIR
from engine.truth import HARDSHIP_COUNTY, Truth

FILES = {
    "state_wage_records": "state_wage_records.json",
    "snap": "snap.json",
    "student_enrollment": "school.json",
    "corrections": "corrections.json",
    "va": "va.json",
    "child_welfare": "child_welfare.json",
    "county": "county.json",
}

INSTITUTIONS = ["Los Angeles City College", "Fresno City College", "San Diego Mesa College", "De Anza College",
                "Sacramento City College", "Santa Ana College", "Cal State Long Beach", "UC Riverside"]

# Records the golden fixtures cite by id (fixtures/golden_cases.json source_ref.record_id).
GOLDEN_RECORD_IDS = {
    ("state_wage_records", "g-omar"): "wr-omar-2027-01",
    ("state_wage_records", "g-deshawn"): "wr-deshawn-2027-01",
    ("state_wage_records", "g-bea"): "wr-bea-2027-01",
    ("snap", "g-bea"): "snap-bea",
    ("child_welfare", "g-deshawn"): "cw-deshawn",
}


def _rid(db: str, pid: str, prefix: str) -> str:
    return GOLDEN_RECORD_IDS.get((db, pid), f"{prefix}-{pid.removeprefix('p-').removeprefix('g-')}")


def build(truths: list[Truth], counties: dict[str, str]) -> dict[str, dict]:
    """All databases as {db_name: {patient_id: record}}. counties maps patient_id -> county name."""
    dbs: dict[str, dict] = {name: {} for name in FILES}
    for i, t in enumerate(sorted(truths, key=lambda t: t.patient_id)):
        pid = t.patient_id
        if t.wage_monthly is not None:
            dbs["state_wage_records"][pid] = {"record_id": _rid("state_wage_records", pid, "wr"),
                                              "quarter": "2026-Q4", "monthly_income": t.wage_monthly}
        if t.snap_enrolled:
            dbs["snap"][pid] = {"record_id": _rid("snap", pid, "snap"), "enrolled": True,
                                "work_requirement_met": bool(t.facts["snap_tanf_work_compliant"].value)}
        if t.school_status:
            dbs["student_enrollment"][pid] = {"record_id": _rid("student_enrollment", pid, "se"),
                                              "institution": INSTITUTIONS[i % len(INSTITUTIONS)],
                                              "term": "Spring 2027", "enrollment_status": t.school_status}
        if t.release_days_ago is not None:
            released = AS_OF_DATE.toordinal() - t.release_days_ago
            dbs["corrections"][pid] = {"record_id": _rid("corrections", pid, "cdcr"),
                                       "release_date": AS_OF_DATE.fromordinal(released).isoformat()}
        if t.va_rating is not None:
            dbs["va"][pid] = {"record_id": _rid("va", pid, "va"), "combined_rating": t.va_rating,
                              "total_disability": t.va_rating == 100}
        if t.facts["former_foster_youth"].value or ("child_welfare", pid) in GOLDEN_RECORD_IDS:
            dbs["child_welfare"][pid] = {"record_id": _rid("child_welfare", pid, "cw"),
                                         "former_foster_youth": bool(t.facts["former_foster_youth"].value)}
        county = counties[pid]
        dbs["county"][pid] = {"record_id": _rid("county", pid, "cty"), "county": county,
                              "hardship": county == HARDSHIP_COUNTY,
                              "reason": "Unemployment above 150% of the national rate (synthetic)"
                                        if county == HARDSHIP_COUNTY else None}
    return dbs


def write(dbs: dict[str, dict]) -> None:
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    for name, records in dbs.items():
        (EXTERNAL_DIR / FILES[name]).write_text(json.dumps(records, indent=1, sort_keys=True) + "\n")
    load.cache_clear()


@cache
def load(db: str) -> dict[str, dict]:
    """One database, by its registry name (e.g. "student_enrollment")."""
    return json.loads((EXTERNAL_DIR / FILES[db]).read_text())


def lookup(db: str, patient_id: str) -> dict | None:
    return load(db).get(patient_id)
