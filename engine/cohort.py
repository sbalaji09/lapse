"""Synthea FHIR -> Patient: what the clinic and the state know about each person before any reasoning.

Patient is Track A's internal model (not a PROJECT.md contract). It carries the raw record: demographics,
conditions, visits in the lookback window, and billing claims with sequenced diagnoses. Sequence 1 is the
primary diagnosis, which is all the state's ex parte check reads.

The golden patients have no Synthea bundle; they are rebuilt from fixtures/golden_cases.json so they flow
through the same pipeline as everyone else.
"""
import hashlib
import json
import re
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

from engine.config import AS_OF_DATE, CITY_COUNTY_PATH, CLINICS, LOOKBACK_START, SEED, SYNTHEA_DIR

COHORT_SIZE = 1000
SPANISH_TARGET = 0.25
RENEWAL_WINDOW_DAYS = 182           # renewals are rolling over the next 6 months
CLAIM_HISTORY_START = AS_OF_DATE - timedelta(days=730)   # keep 2 years; Channel A applies the pack's lookback

AI_AN = "1002-5"                    # OMB race category: American Indian or Alaska Native
MILITARY_SERVICE = "1187604002"     # Synthea social finding: Serving in military service
CRIMINAL_RECORD = "266948004"       # Synthea social finding: Has a criminal record


class Dx(BaseModel):
    code: str
    display: str
    sequence: int


class BilledClaim(BaseModel):
    id: str
    date: date
    kind: str                       # professional | institutional
    diagnoses: list[Dx]


class Condition(BaseModel):
    code: str
    display: str
    onset: date | None
    abatement: date | None

    def active_on(self, day: date) -> bool:
        return (self.onset is None or self.onset <= day) and (self.abatement is None or self.abatement >= day)

    def active_during(self, start: date, end: date) -> bool:
        return (self.onset is None or self.onset <= end) and (self.abatement is None or self.abatement >= start)


class Encounter(BaseModel):
    id: str
    date: date
    klass: str                      # AMB | EMER | IMP | ...
    type: str
    reason_code: str | None
    reason: str | None


class Patient(BaseModel):
    id: str
    source_id: str                  # Synthea Patient.id, or the golden id
    given: str
    family: str
    display_name: str
    sex: str                        # female | male
    birth_date: date
    age: int
    races: list[str]
    ethnicity: str | None
    ai_an: bool
    language: str
    city: str
    county: str
    email: str
    phone: str | None
    clinic_id: str
    clinician_name: str
    renewal_date: date
    conditions: list[Condition]
    encounters: list[Encounter]     # lookback window only
    claims: list[BilledClaim]       # with at least one diagnosis, last 2 years
    military_service: bool = False
    criminal_record: bool = False
    golden: bool = False

    def billed_dx(self, since: date = LOOKBACK_START) -> list[dict]:
        """Rows for Case.billed_dx_12mo: everything billed since `since`, primary and secondary."""
        return [{"claim_id": c.id, "date": c.date.isoformat(), "code": d.code, "system": "SNOMED",
                 "display": d.display, "sequence": d.sequence}
                for c in sorted(self.claims, key=lambda c: c.date) if c.date >= since for d in c.diagnoses]


# ---------------------------------------------------------------------------------------------
# FHIR parsing
# ---------------------------------------------------------------------------------------------

def _day(s: str | None) -> date | None:
    return date.fromisoformat(s[:10]) if s else None


def _clean_name(s: str) -> str:
    return re.sub(r"\d+", "", s).strip()


def _clean_display(s: str) -> str:
    """'Acute bronchitis (disorder)' -> 'Acute bronchitis'."""
    return re.sub(r"\s*\((disorder|finding|situation|procedure|regime/therapy|person|morphologic abnormality|"
                  r"environment)\)$", "", s).strip()


def age_on(birth: date, day: date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def parse_bundle(path: Path) -> dict | None:
    """One Synthea bundle -> Patient fields, or None for patients who died in the simulation."""
    entries = json.loads(Path(path).read_text())["entry"]
    by_url = {e["fullUrl"]: e["resource"] for e in entries}
    p = entries[0]["resource"]
    assert p["resourceType"] == "Patient", path
    if "deceasedDateTime" in p:
        return None

    races, ethnicity = [], None
    for ext in p.get("extension", []):
        for sub in ext.get("extension", []):
            if sub["url"] != "ombCategory":
                continue
            if ext["url"].endswith("us-core-race"):
                races.append(sub["valueCoding"]["code"])
            elif ext["url"].endswith("us-core-ethnicity"):
                ethnicity = sub["valueCoding"]["display"]

    name = next((n for n in p["name"] if n.get("use") == "official"), p["name"][0])
    lang = p.get("communication", [{}])[0].get("language", {}).get("coding", [{}])[0].get("code", "en")
    phone = next((t["value"] for t in p.get("telecom", []) if t["system"] == "phone"), None)

    conditions, cond_codes = [], set()
    encounters, claims = [], []
    for r in by_url.values():
        kind = r["resourceType"]
        if kind == "Condition":
            c = r["code"]["coding"][0]
            conditions.append(Condition(code=c["code"], display=_clean_display(c["display"]),
                                        onset=_day(r.get("onsetDateTime")), abatement=_day(r.get("abatementDateTime"))))
            cond_codes.add(c["code"])
        elif kind == "Encounter":
            d = _day(r["period"]["start"])
            if LOOKBACK_START <= d <= AS_OF_DATE:
                reason = (r.get("reasonCode") or [{}])[0].get("coding", [{}])[0]
                encounters.append(Encounter(id=r["id"], date=d, klass=r["class"]["code"],
                                            type=_clean_display(r["type"][0]["coding"][0]["display"]),
                                            reason_code=reason.get("code"),
                                            reason=_clean_display(reason["display"]) if reason.get("display") else None))
        elif kind == "Claim" and r.get("diagnosis"):
            d = _day(r["billablePeriod"]["start"])
            if not (CLAIM_HISTORY_START <= d <= AS_OF_DATE):
                continue
            dxs = []
            for dx in r["diagnosis"]:
                cond = by_url.get(dx.get("diagnosisReference", {}).get("reference"))
                if cond is None:
                    continue
                c = cond["code"]["coding"][0]
                dxs.append(Dx(code=c["code"], display=_clean_display(c["display"]), sequence=dx["sequence"]))
            if dxs:
                claims.append(BilledClaim(id=r["id"], date=d, kind=r["type"]["coding"][0]["code"],
                                          diagnoses=sorted(dxs, key=lambda x: x.sequence)))

    birth = date.fromisoformat(p["birthDate"])
    return {
        "source_id": p["id"],
        "given": _clean_name(name["given"][0]),
        "family": _clean_name(name["family"]),
        "sex": p["gender"],
        "birth_date": birth,
        "age": age_on(birth, AS_OF_DATE),
        "races": races,
        "ethnicity": ethnicity,
        "ai_an": AI_AN in races,
        "language": lang.split("-")[0].lower(),
        "city": p["address"][0]["city"],
        "phone": phone,
        "conditions": conditions,
        "encounters": sorted(encounters, key=lambda e: e.date),
        "claims": sorted(claims, key=lambda c: c.date),
        "military_service": MILITARY_SERVICE in cond_codes,
        "criminal_record": CRIMINAL_RECORD in cond_codes,
    }


# ---------------------------------------------------------------------------------------------
# Administrative assignment (clinic, renewal, language, contact) - seeded per patient
# ---------------------------------------------------------------------------------------------

def _unit(*parts) -> float:
    """Deterministic uniform [0, 1) from a key; stable across runs and Python versions."""
    h = hashlib.sha256(":".join(map(str, (SEED, *parts))).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace(" ", "")


def _assign(fields: dict, county_of: dict[str, str]) -> Patient:
    sid = fields["source_id"]
    pid = "p-" + sid.replace("-", "")[:8]
    clinic_id = sorted(CLINICS)[int(_unit("clinic", sid) * len(CLINICS))]
    renewal = AS_OF_DATE + timedelta(days=1 + int(_unit("renewal", sid) * RENEWAL_WINDOW_DAYS))
    return Patient(
        id=pid,
        display_name=f"{fields['given']} {fields['family']}",
        county=county_of[fields["city"]],
        email=f"{_ascii(fields['given'])}.{_ascii(fields['family'])}.{pid[2:6]}@example.com",
        clinic_id=clinic_id,
        clinician_name=CLINICS[clinic_id]["clinician"],
        renewal_date=renewal,
        **fields,
    )


def _balance_spanish(patients: list[Patient], total: int) -> None:
    """Synthea gives ~22% Spanish; the demo cohort wants ~25%. Switch the needed number of Hispanic
    English speakers, chosen by a seeded hash so the choice is stable."""
    need = round(SPANISH_TARGET * total) - sum(p.language == "es" for p in patients)
    pool = sorted((p for p in patients if p.language == "en" and p.ethnicity == "Hispanic or Latino"),
                  key=lambda p: _unit("spanish", p.source_id))
    for p in pool[:max(need, 0)]:
        p.language = "es"


# ---------------------------------------------------------------------------------------------
# Golden patients, rebuilt from fixtures
# ---------------------------------------------------------------------------------------------

# Facts about the golden patients that the fixture Case does not carry. Counties avoid the hardship county.
GOLDEN_EXTRA = {
    "g-rosa": {"sex": "female", "county": "Los Angeles", "city": "Los Angeles", "ethnicity": "Hispanic or Latino",
               "conditions": [("44054006", "Diabetes mellitus type 2", "2016-03-02"),
                              ("368581000119106", "Neuropathy due to type 2 diabetes mellitus", "2025-06-10")]},
    "g-marcus": {"sex": "male", "county": "Los Angeles", "city": "Los Angeles", "ethnicity": "Not Hispanic or Latino",
                 "conditions": [("185086009", "Chronic obstructive bronchitis", "2017-09-14")]},
    "g-deshawn": {"sex": "male", "county": "Sacramento", "city": "Sacramento", "ethnicity": "Not Hispanic or Latino",
                  "conditions": []},
    "g-linh": {"sex": "female", "county": "Orange", "city": "Westminster", "ethnicity": "Not Hispanic or Latino",
               "conditions": [("59621000", "Essential hypertension", "2014-05-20"),
                              ("431857002", "Chronic kidney disease stage 4", "2025-01-08")]},
    "g-karen": {"sex": "female", "county": "San Diego", "city": "San Diego", "ethnicity": "Not Hispanic or Latino",
                "conditions": [("370143000", "Major depressive disorder", "2012-11-01")]},
    "g-omar": {"sex": "male", "county": "Alameda", "city": "Oakland", "ethnicity": "Not Hispanic or Latino",
               "conditions": []},
    "g-bea": {"sex": "female", "county": "Riverside", "city": "Riverside", "ethnicity": "Not Hispanic or Latino",
              "conditions": [("59621000", "Essential hypertension", "2019-02-11"),
                             ("714628002", "Prediabetes", "2024-10-01")]},
}


def golden_patients() -> list[Patient]:
    from engine.store import read_fixtures   # local import: store imports nothing from here, keep it that way

    out = []
    for case, notes in read_fixtures():
        extra = GOLDEN_EXTRA[case.patient_id]
        claims: dict[str, BilledClaim] = {}
        for row in case.billed_dx_12mo:
            c = claims.setdefault(row["claim_id"], BilledClaim(id=row["claim_id"], date=date.fromisoformat(row["date"]),
                                                               kind="professional", diagnoses=[]))
            c.diagnoses.append(Dx(code=row["code"], display=row["display"], sequence=row["sequence"]))
        given, family = case.display_name.split(" ", 1)
        birth = date(AS_OF_DATE.year - case.age - 1, 7, 1)
        out.append(Patient(
            id=case.patient_id, source_id=case.patient_id, given=given, family=family,
            display_name=case.display_name, sex=extra["sex"], birth_date=birth, age=age_on(birth, AS_OF_DATE),
            races=["2106-3"], ethnicity=extra["ethnicity"], ai_an=False, language=case.language,
            city=extra["city"], county=extra["county"], email=case.email, phone=case.phone,
            clinic_id=case.clinic_id, clinician_name=case.clinician_name, renewal_date=case.renewal_date,
            conditions=[Condition(code=c, display=d, onset=date.fromisoformat(o), abatement=None)
                        for c, d, o in extra["conditions"]],
            encounters=[Encounter(id=f"e-{n.id}", date=n.date, klass="AMB", type="Encounter for problem",
                                  reason_code=None, reason=None) for n in notes],
            claims=list(claims.values()), golden=True,
        ))
        assert out[-1].age == case.age, case.patient_id
    return out


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------

def load_cohort(synthea_dir: Path = SYNTHEA_DIR, workers: int = 8) -> list[Patient]:
    """993 living Synthea adults aged 19-64 plus the 7 golden patients, sorted by id."""
    county_of = json.loads(CITY_COUNTY_PATH.read_text())
    paths = sorted(synthea_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no Synthea bundles in {synthea_dir}; see TRACK_A.md A1 step 1")
    with ProcessPoolExecutor(workers) as pool:
        parsed = [f for f in pool.map(parse_bundle, paths, chunksize=16) if f is not None]

    adults = [f for f in parsed if 19 <= f["age"] <= 64]
    goldens = golden_patients()
    adults = adults[:COHORT_SIZE - len(goldens)]
    patients = [_assign(f, county_of) for f in adults]
    ids = [p.id for p in patients]
    if len(set(ids)) != len(ids):
        raise ValueError("patient id collision; lengthen the id prefix in _assign")
    _balance_spanish(patients, COHORT_SIZE)
    return sorted(patients + goldens, key=lambda p: p.id)
