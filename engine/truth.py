"""Ground truth: who is really exempt or compliant, and why, and who knows each fact.

Owning the truth is what makes the accuracy chart possible. Every patient gets a seeded draw of the
social facts the rules care about, plus a medically-frail truth derived from their real Synthea
conditions and an injected impairment level. engine/notes.py writes the chart from this truth, and
engine/external.py writes the mock databases from it; neither may invent facts of its own.

Each truth fact records its holders (who could tell us) and whether the state can see it.
"""
import json
from functools import cache
from typing import Literal

import yaml
from pydantic import BaseModel

from engine.cohort import Patient, _unit
from engine.config import AS_OF_DATE, FACT_REGISTRY_PATH, LOOKBACK_START, QUALIFYING_CONDITIONS_PATH, TRUTH_DIR

HARDSHIP_COUNTY = "Fresno"
PREGNANCY_CODES = {"72892002"}          # Normal pregnancy; postpartum counts for 12 months after it ends
FEDERAL_MIN_WAGE_80H = 580
RELEASE_WINDOW_DAYS = 90


class TruthFact(BaseModel):
    value: bool | int | None
    holders: list[str]                  # who could tell us: database names, "clinician", "patient"
    state_visible: bool


class GroupTruth(BaseModel):
    group: str                          # key in data/qualifying_conditions.yaml
    category: str
    condition: str
    physical: bool
    codes: list[str]                    # the patient's active codes in this group
    impairs: bool
    documentation: Literal["positive", "unstated"]   # what the notes will say about function


class Truth(BaseModel):
    patient_id: str
    facts: dict[str, TruthFact]
    frail_groups: list[GroupTruth]
    function_normal_note: bool          # notes say explicitly that the person has no limitations
    sud_treatment_documented: bool      # notes mention the treatment program
    wage_monthly: int | None            # what state wage records show (None = no record)
    snap_enrolled: bool
    school_status: str | None           # half_time | full_time | less_than_half_time
    release_days_ago: int | None
    va_rating: int | None
    exempt_or_compliant: bool
    reasons: list[str]                  # rule ids from the pack that the truth satisfies


@cache
def qualifying_groups() -> dict[str, dict]:
    return yaml.safe_load(QUALIFYING_CONDITIONS_PATH.read_text())["groups"]


@cache
def _registry() -> dict:
    return yaml.safe_load(FACT_REGISTRY_PATH.read_text())


def _code_to_group() -> dict[str, str]:
    return {code: g for g, spec in qualifying_groups().items() for code in spec["codes"]}


def _pick(key: tuple, options: list):
    return options[int(_unit(*key) * len(options))]


def _holders(key: str, has_record: bool = True) -> list[str]:
    """Registry holder order, with the database dropped when this person has no record there."""
    spec = _registry()[key]
    out = []
    for h in spec["holders"]:
        if h == "database":
            if has_record:
                out.append(spec["database"])
        else:
            out.append(h)
    return out


def evaluate(facts: dict[str, TruthFact], age: int) -> list[str]:
    """Rule ids (CA pack) satisfied by these facts. Written out plainly on purpose: it is the answer key
    the rule-pack evaluator gets scored against, so it must not share code with it."""
    v = {k: f.value for k, f in facts.items()}
    rules = [
        ("hours", (v["hours_per_month"] or 0) >= 80),
        ("income", (v["monthly_income"] or 0) >= FEDERAL_MIN_WAGE_80H),
        ("school", bool(v["enrolled_half_time_school"])),
        ("medically_frail", bool(v["qualifying_condition"]) and bool(v["significantly_impairs"])),
        ("parent_caretaker", bool(v["dependent_child_13_or_under"]) or bool(v["caregiver_disabled_person"])),
        ("pregnant_postpartum", bool(v["pregnant_or_postpartum"])),
        ("ai_an", bool(v["ai_an"])),
        ("veteran_disability", bool(v["veteran_total_disability"])),
        ("snap_tanf", bool(v["snap_tanf_work_compliant"])),
        ("sud_treatment", bool(v["in_sud_treatment"])),
        ("recent_release", v["released_incarceration_days"] is not None
                           and v["released_incarceration_days"] <= RELEASE_WINDOW_DAYS),
        ("foster_youth", bool(v["former_foster_youth"]) and age < 26),
        ("hardship_county", bool(v["county_hardship"])),
    ]
    return [rule for rule, ok in rules if ok]


def generate(p: Patient) -> Truth:
    if p.golden:
        return golden_truth(p)
    u = lambda purpose: _unit("truth", purpose, p.source_id)   # noqa: E731 - one seeded draw per purpose

    # Work and income. Wage records are what the state sees; informal work is known only to the person.
    r = u("wages")
    wage = None
    if r < 0.15:
        wage = 600 + int(u("wage_hi") * 1800)
    elif r < 0.35:
        wage = 50 + int(u("wage_lo") * 500)
    informal = wage is None or wage < FEDERAL_MIN_WAGE_80H
    informal = informal and u("informal") < 0.10 / 0.85
    if informal:
        hours = 80 + int(u("hours") * 80)
        income = (wage or 0) + hours * 12
    else:
        hours = round(wage / 16.5) if wage else (0 if u("idle") < 0.6 else int(u("hours_lo") * 60))
        income = wage or 0

    # Programs and records.
    snap_enrolled = u("snap") < 0.18
    snap_compliant = snap_enrolled and u("snap_work") < 0.05 / 0.18
    school_p = 0.15 if p.age < 26 else 0.04 if p.age < 36 else 0.01
    rs = u("school")
    school = "half_time" if rs < school_p * 0.6 else "full_time" if rs < school_p else \
        "less_than_half_time" if rs < school_p + 0.01 else None
    release_p = 0.025 if p.criminal_record else 0.007
    rr = u("release")
    release_days = (1 + int(u("release_days") * RELEASE_WINDOW_DAYS)) if rr < release_p else \
        (RELEASE_WINDOW_DAYS + 1 + int(u("release_old") * 600)) if rr < release_p + 0.02 else None
    va_p = 0.2 if p.military_service else 0.007
    rv = u("va")
    va_rating = 100 if rv < va_p else _pick(("va_rating", p.source_id), [10, 30, 50, 70]) if rv < va_p + 0.02 else None
    foster = p.age < 26 and u("foster") < 0.01

    child_p = 0.10 if p.age < 25 else 0.22 if p.age < 45 else 0.08 if p.age < 55 else 0.02
    child = u("child") < child_p
    caregiver = u("caregiver") < 0.03
    pregnant = any(c.code in PREGNANCY_CODES and c.onset and c.onset <= AS_OF_DATE
                   and (c.abatement is None or (AS_OF_DATE - c.abatement).days <= 365) for c in p.conditions)

    # Medically frail: real conditions, injected impairment.
    groups = qualifying_groups()
    by_code = _code_to_group()
    active: dict[str, list[str]] = {}
    for c in p.conditions:
        g = by_code.get(c.code)
        if g and c.active_during(LOOKBACK_START, AS_OF_DATE):
            active.setdefault(g, []).append(c.code)
    frail = []
    for g, codes in sorted(active.items()):
        spec = groups[g]
        impairs = u(f"impairs:{g}") < spec["p_impairs"]
        documented = impairs and u(f"documented:{g}") < 0.45
        frail.append(GroupTruth(group=g, category=spec["category"], condition=spec["condition"],
                                physical=spec["physical"], codes=sorted(set(codes)), impairs=impairs,
                                documentation="positive" if documented else "unstated"))
    qualifying = bool(frail)
    impaired = any(g.impairs for g in frail)
    standing = None
    if any(g.physical for g in frail):
        standing = _pick(("standing", p.source_id), [5, 10, 10, 15, 15, 20, 25]) \
            if any(g.physical and g.impairs for g in frail) else \
            _pick(("standing", p.source_id), [45, 60, 90, 120, 180, 240])

    sud_group = "sud" in active
    in_sud = u("sud_treatment") < (0.35 if sud_group else 0.005)

    facts = {
        "monthly_income": TruthFact(value=income, holders=_holders("monthly_income", wage is not None),
                                    state_visible=wage is not None),
        "hours_per_month": TruthFact(value=hours, holders=_holders("hours_per_month"), state_visible=False),
        "enrolled_half_time_school": TruthFact(value=school in ("half_time", "full_time"),
                                               holders=_holders("enrolled_half_time_school", school is not None),
                                               state_visible=False),
        "snap_tanf_work_compliant": TruthFact(value=snap_compliant, holders=_holders("snap_tanf_work_compliant",
                                                                                     snap_enrolled),
                                              state_visible=snap_enrolled),
        "in_sud_treatment": TruthFact(value=in_sud, holders=_holders("in_sud_treatment"), state_visible=False),
        "released_incarceration_days": TruthFact(value=release_days,
                                                 holders=_holders("released_incarceration_days", release_days is not None),
                                                 state_visible=release_days is not None),
        "dependent_child_13_or_under": TruthFact(value=child, holders=_holders("dependent_child_13_or_under"),
                                                 state_visible=False),
        "caregiver_disabled_person": TruthFact(value=caregiver, holders=_holders("caregiver_disabled_person"),
                                               state_visible=False),
        "pregnant_or_postpartum": TruthFact(value=pregnant, holders=_holders("pregnant_or_postpartum"),
                                            state_visible=pregnant),
        "ai_an": TruthFact(value=p.ai_an, holders=_holders("ai_an"), state_visible=True),
        "veteran_total_disability": TruthFact(value=va_rating == 100,
                                              holders=_holders("veteran_total_disability", va_rating is not None),
                                              state_visible=False),
        "former_foster_youth": TruthFact(value=foster, holders=_holders("former_foster_youth", foster),
                                         state_visible=False),
        "county_hardship": TruthFact(value=p.county == HARDSHIP_COUNTY, holders=_holders("county_hardship"),
                                     state_visible=False),
        "qualifying_condition": TruthFact(value=qualifying, holders=["clinician"] if qualifying else [],
                                          state_visible=False),
        "significantly_impairs": TruthFact(value=impaired, holders=["clinician", "patient"] if qualifying else [],
                                           state_visible=False),
        "limitation_attested": TruthFact(value=qualifying and impaired, holders=["clinician"], state_visible=False),
    }
    if standing is not None:
        facts["standing_tolerance_minutes"] = TruthFact(value=standing, holders=["patient", "clinician"],
                                                        state_visible=False)

    reasons = evaluate(facts, p.age)
    return Truth(
        patient_id=p.id, facts=facts, frail_groups=frail,
        function_normal_note=(not impaired) and u("function_normal") < (0.3 if qualifying else 0.1),
        sud_treatment_documented=in_sud and u("sud_documented") < 0.7,
        wage_monthly=wage, snap_enrolled=snap_enrolled, school_status=school, release_days_ago=release_days,
        va_rating=va_rating, exempt_or_compliant=bool(reasons), reasons=reasons,
    )


# ---------------------------------------------------------------------------------------------
# Golden truth: hand-set so each hero patient tells its story (PROJECT.md golden fixtures table)
# ---------------------------------------------------------------------------------------------

_NO = {"monthly_income": 0, "hours_per_month": 0, "enrolled_half_time_school": False,
       "snap_tanf_work_compliant": False, "in_sud_treatment": False, "released_incarceration_days": None,
       "dependent_child_13_or_under": False, "caregiver_disabled_person": False, "pregnant_or_postpartum": False,
       "ai_an": False, "veteran_total_disability": False, "former_foster_youth": False, "county_hardship": False,
       "qualifying_condition": False, "significantly_impairs": False, "limitation_attested": False}

GOLDEN_TRUTH = {
    # Stopped working in March; her own words (B3's reply) say she can stand about ten minutes.
    "g-rosa": {"values": {"qualifying_condition": True, "significantly_impairs": True, "limitation_attested": True,
                          "standing_tolerance_minutes": 10},
               "groups": [("diabetic_neuropathy", ["368581000119106"], True, "unstated")]},
    "g-marcus": {"values": {"qualifying_condition": True, "significantly_impairs": True, "limitation_attested": True,
                            "standing_tolerance_minutes": 5},
                 "groups": [("copd", ["185086009"], True, "positive")]},
    "g-deshawn": {"values": {"enrolled_half_time_school": True}, "wage": 0, "school": "half_time"},
    "g-linh": {"values": {"qualifying_condition": True, "significantly_impairs": True, "limitation_attested": True,
                          "standing_tolerance_minutes": 20},
               "groups": [("ckd_advanced", ["431857002"], True, "positive")]},
    # Works full time, paid as a contractor, so no state wage record: truly compliant on hours, yet the state
    # exempts her on a depression code her chart does not support. Correct outcome, fragile basis.
    "g-karen": {"values": {"qualifying_condition": True, "hours_per_month": 160, "monthly_income": 3800},
                "groups": [("mental_illness", ["370143000"], False, "unstated")], "function_normal": True},
    "g-omar": {"values": {"monthly_income": 920, "hours_per_month": 56}, "wage": 920},
    "g-bea": {"values": {}, "wage": 0, "snap": True},
}


def golden_truth(p: Patient) -> Truth:
    spec = GOLDEN_TRUTH[p.id]
    values = {**_NO, **spec["values"]}
    wage = spec.get("wage")
    school = spec.get("school")
    groups = qualifying_groups()
    frail = [GroupTruth(group=g, category=groups[g]["category"], condition=groups[g]["condition"],
                        physical=groups[g]["physical"], codes=codes, impairs=imp, documentation=doc)
             for g, codes, imp, doc in spec.get("groups", [])]
    state_visible = {"monthly_income": wage is not None, "snap_tanf_work_compliant": spec.get("snap", False),
                     "ai_an": True}
    has_record = {"monthly_income": wage is not None, "enrolled_half_time_school": school is not None,
                  "snap_tanf_work_compliant": spec.get("snap", False)}
    facts = {k: TruthFact(value=v, holders=_holders(k, has_record.get(k, False)) if k in _registry() else ["clinician"],
                          state_visible=state_visible.get(k, False))
             for k, v in values.items()}
    reasons = evaluate(facts, p.age)
    return Truth(patient_id=p.id, facts=facts, frail_groups=frail,
                 function_normal_note=spec.get("function_normal", False), sud_treatment_documented=False,
                 wage_monthly=wage, snap_enrolled=spec.get("snap", False), school_status=school,
                 release_days_ago=None, va_rating=None, exempt_or_compliant=bool(reasons), reasons=reasons)


def write_truth(truths: list[Truth]) -> None:
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    for old in TRUTH_DIR.glob("*.json"):
        old.unlink()
    for t in truths:
        (TRUTH_DIR / f"{t.patient_id}.json").write_text(t.model_dump_json(indent=1) + "\n")


def load_truth(patient_id: str) -> Truth:
    return Truth.model_validate(json.loads((TRUTH_DIR / f"{patient_id}.json").read_text()))
