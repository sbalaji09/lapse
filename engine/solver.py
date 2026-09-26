"""Counterfactual solver: which single missing fact would clear this person, and who holds it. Zero LLM.

For every rule in the pack: a known-false term kills the rule; otherwise its unknown terms are its missing set.
A rule missing exactly one term offers candidates (one per unknown atom of that term). Each candidate gets the
first holder in the fact registry that still applies - a database we have not consulted yet (bother nobody),
then the clinician, then the patient. We never ask a person for something we could look up.

Leads. Hours, children, pregnancy and the like are unknown for almost everyone the state cannot clear; that is
the generic question the state's own notice already asks. So a person is ONE_AWAY only when the one missing
fact has a lead: a database we can still check, or evidence in their own chart (a documented qualifying
condition with no word on function; a documented substance use disorder that may be under treatment).
Ranking uses only case facts and fixed population priors - never the truth files.

Also exported for the patient loop (Track B):
    reevaluate(case)                  -> recompute final determination, bucket and missing facts after new facts
    resolve_database(case, missing)   -> the Fact a database lookup yields
"""
from datetime import datetime
from functools import cache

import yaml

from engine import buckets, external
from engine.config import FACT_REGISTRY_PATH
from engine.models import Bucket, Case, CaseStatus, Claim, Fact, Holder, MissingFact, Source, Tri
from engine.rulepack import RulePack, load_pack

HOLDER_COST = {Holder.database: 0, Holder.clinician: 1, Holder.patient: 2}

# Population priors for tie-breaking (truth-independent; published-rate style guesses, not fitted to our data).
PRIORS = {"significantly_impairs": 0.5, "standing_tolerance_minutes": 0.5, "qualifying_condition": 0.5,
          "in_sud_treatment": 0.35, "enrolled_half_time_school": 0.04, "county_hardship": 0.03,
          "veteran_total_disability": 0.01, "former_foster_youth": 0.01}

# Categories whose limitation shows up as how long someone can stand or walk.
PHYSICAL = {"physical_disability", "serious_complex_medical", "blind"}

DB_LABELS = {"student_enrollment": "the student enrollment database", "va": "VA disability records",
             "child_welfare": "child welfare records", "county": "the county hardship list"}

# Databases Lapse may query that the state does not read: key -> how a record answers it (no record = false).
DB_ANSWERS = {
    "enrolled_half_time_school": lambda r: r["enrollment_status"] in ("half_time", "full_time"),
    "veteran_total_disability": lambda r: bool(r["total_disability"]),
    "former_foster_youth": lambda r: bool(r["former_foster_youth"]),
    "county_hardship": lambda r: bool(r["hardship"]),
}

RULE_NAMES = {"hours": "80 hours of work or activity", "income": "income", "school": "school enrollment",
              "medically_frail": "medical frailty", "parent_caretaker": "caring for a child or disabled person",
              "pregnant_postpartum": "pregnancy or postpartum", "ai_an": "American Indian / Alaska Native status",
              "veteran_disability": "a total VA disability rating", "snap_tanf": "SNAP work compliance",
              "sud_treatment": "substance use treatment", "recent_release": "recent release from incarceration",
              "foster_youth": "former foster youth status", "hardship_county": "a county hardship designation"}


@cache
def registry() -> dict:
    return yaml.safe_load(FACT_REGISTRY_PATH.read_text())


def first_name(case: Case) -> str:
    return case.display_name.split()[0]


def condition_name(case: Case) -> str:
    """Plain name of the documented qualifying condition, for questions and explanations."""
    claims = [c for c in case.claims if c.verified and c.qualifying_category and c.category != "sud_treatment"]
    if claims:
        return sorted(claims, key=lambda c: c.significantly_impairs != Tri.unknown)[0].condition
    billed = next((f for f in case.facts if f.key == "qualifying_condition" and f.source == Source.billing_code), None)
    return billed.source_ref.get("display", "a qualifying condition").lower() if billed else "a qualifying condition"


def consulted_databases(case: Case, pack: RulePack) -> set[str]:
    """Databases already read for this person: everything the state reads, plus any Lapse looked up."""
    done = {s for s in pack.state_visible_sources if s in external.FILES}
    done |= {f.source_ref.get("db") for f in case.facts if f.source == Source.external_db}
    return done


def _question(key: str, case: Case, condition: str) -> dict[str, str]:
    q = registry()[key].get("question", {"en": key.replace("_", " ")})
    return {lang: text.format(name=first_name(case), condition=condition) for lang, text in q.items()}


def _holder(key: str, case: Case, consulted: set[str]) -> tuple[Holder, str | None] | None:
    spec = registry()[key]
    for h in spec["holders"]:
        if h == "database":
            if spec["database"] not in consulted:
                return Holder.database, spec["database"]
        else:
            return Holder(h), None
    return None


def _why(key: str, rule: str, holder: Holder, db: str | None, case: Case, condition: str) -> str:
    name = first_name(case)
    if key == "standing_tolerance_minutes":
        return (f"{condition[:1].upper() + condition[1:]} is in {name}'s notes, but nothing says how it affects daily "
                f"life. If {name} can't stand for long, that is enough for a medical frailty exemption.")
    if key == "significantly_impairs":
        return (f"{condition[:1].upper() + condition[1:]} is in {name}'s notes, but nothing says how it affects daily "
                f"life. {name}'s own description of the impact can establish medical frailty.")
    if key == "limitation_attested":
        return (f"{name}'s record shows {condition} limiting daily activity. "
                f"{case.clinician_name} only needs to confirm and sign.")
    if key == "in_sud_treatment":
        return (f"Notes document a substance use disorder. If {name} is in a treatment program now, that alone is "
                f"an exemption; {case.clinician_name} can confirm.")
    if holder == Holder.database:
        return f"{DB_LABELS.get(db, db)} can confirm {RULE_NAMES[rule]} without contacting anyone."
    return f"Would qualify through {RULE_NAMES[rule]}."


def _missing(case: Case, key: str, holder: Holder, db: str | None, rule: str, condition: str) -> MissingFact:
    return MissingFact(id=f"m-{case.patient_id}-{key}", patient_id=case.patient_id, key=key, holder=holder,
                       database=db, unlocks_rule=rule, why=_why(key, rule, holder, db, case, condition),
                       question=_question(key, case, condition))


def solve(case: Case, pack: RulePack) -> tuple[list[MissingFact], bool]:
    """(missing facts best first, has_lead) for a person nobody has cleared yet."""
    vals = buckets.values(case.facts)
    consulted = consulted_databases(case, pack)
    condition = condition_name(case)
    sud_documented = any(c.verified and c.category == "sud" for c in case.claims)
    physical = any(c.verified and c.qualifying_category and c.category in PHYSICAL for c in case.claims) or \
        any(f.key == "qualifying_condition" and f.source == Source.billing_code for f in case.facts)

    candidates = []    # (cost, -prior, key, holder, db, rule, lead)
    for rule in pack.rules:
        unknown = rule.unknown_terms(vals)
        if rule.evaluate(vals) != Tri.unknown or len(unknown) != 1:
            continue
        for atom in unknown[0].atoms:
            if atom.evaluate(vals) != Tri.unknown:
                continue
            key = atom.key
            if rule.id == "medically_frail" and key == "significantly_impairs":
                # A documented condition with nothing on function: ask the person how it limits them.
                key = "standing_tolerance_minutes" if physical else "significantly_impairs"
                candidates.append((HOLDER_COST[Holder.patient], -PRIORS[key], key, Holder.patient, None, rule.id, True))
                continue
            found = _holder(key, case, consulted)
            if found is None:
                continue
            holder, db = found
            lead = holder == Holder.database or (rule.id == "medically_frail") or \
                (rule.id == "sud_treatment" and sud_documented)
            candidates.append((HOLDER_COST[holder], -PRIORS.get(key, 0.05), key, holder, db, rule.id, lead))

    leads = sorted(c for c in candidates if c[6])
    out: list[MissingFact] = []
    for cost, _, key, holder, db, rule, _ in leads:
        if any(m.key == key for m in out):
            continue
        out.append(_missing(case, key, holder, db, rule, condition))
        if rule == "medically_frail" and not any(m.key == "limitation_attested" for m in out):
            out.append(_missing(case, "limitation_attested", Holder.clinician, None, rule, condition))
    return out, bool(leads)


def signoffs(case: Case, det_final, pack: RulePack) -> list[MissingFact]:
    """PROVABLE: the clinician attestation that turns chart evidence into a medical exemption attestation."""
    condition = condition_name(case)
    current = buckets.current_facts(case.facts)
    out = []
    for rule in det_final.rule_ids:
        if rule == "medically_frail" and not (current.get("limitation_attested") and current["limitation_attested"].value):
            out.append(_missing(case, "limitation_attested", Holder.clinician, None, rule, condition))
        if rule == "sud_treatment" and current["in_sud_treatment"].source != Source.clinician_attestation:
            out.append(_missing(case, "in_sud_treatment", Holder.clinician, None, rule, condition))
    return out


def reevaluate(case: Case, pack: RulePack | None = None) -> Case:
    """Recompute determination_final, bucket and missing from case.facts. Missing facts already asked or
    answered are kept (with their ids and status); open ones are replaced by the fresh solver output.
    Status is left to the caller: it tracks workflow (asked, waiting), which the rules cannot see."""
    pack = pack or load_pack()
    case = case.model_copy(deep=True)
    case.determination_final = buckets.final_determination(case.patient_id, case.facts, pack)
    fresh, has_lead = [], False
    if case.determination_a.status == "not_determined":
        if case.determination_final.status == "not_determined":
            fresh, has_lead = solve(case, pack)
        else:
            fresh = signoffs(case, case.determination_final, pack)
    case.bucket = buckets.bucket(case.determination_a, case.determination_final, case.facts, has_lead, pack)
    if case.bucket in (Bucket.SAFE, Bucket.NO_PATH):
        fresh = []
    kept = [m for m in case.missing if m.status != "open" or any(f.key == m.key for f in fresh)]
    case.missing = kept + [m for m in fresh if not any(k.key == m.key for k in kept)]
    return case


def resolve_database(case: Case, missing: MissingFact, at: datetime | None = None) -> Fact:
    """Look the fact up in its database. No record means false (the person is not in that system)."""
    if missing.holder != Holder.database or missing.key not in DB_ANSWERS:
        raise ValueError(f"{missing.key} is not answerable from a database Lapse can query")
    rec = external.lookup(missing.database, case.patient_id)
    value = DB_ANSWERS[missing.key](rec) if rec else False
    return Fact(id=f"fd-{case.patient_id}-{missing.key}", patient_id=case.patient_id, key=missing.key, value=value,
                source=Source.external_db,
                source_ref={"db": missing.database, "record_id": rec["record_id"] if rec else None},
                recorded_at=at or datetime.now(), rule_pack_version=load_pack().version)


def settle(case: Case, pack: RulePack | None = None, keep_open: tuple[str, ...] = (),
           at: datetime | None = None) -> tuple[Case, list[str]]:
    """Reevaluate, then answer every open database-held fact by lookup, repeating until nothing is left to look
    up - so nobody is asked anything a database could answer. Keys in keep_open are left for a person to click.
    Returns (case, databases that answered yes). Lookups that answer no are recorded as facts, not kept as
    missing items."""
    pack = pack or load_pack()
    hits: list[str] = []
    while True:
        case = reevaluate(case, pack)
        lookups = [m for m in case.missing if m.status == "open" and m.holder == Holder.database
                   and m.key not in keep_open]
        if not lookups:
            return case, hits
        for m in lookups:
            fact = resolve_database(case, m, at=at)
            case.facts.append(fact)
            m.status = "resolved_true" if fact.value else "resolved_false"
            if fact.value:
                hits.append(m.database)
        case.missing = [m for m in case.missing if m.status != "resolved_false"]


def default_status(case: Case) -> CaseStatus:
    return CaseStatus.needs_action if case.bucket in (Bucket.PROVABLE, Bucket.ONE_AWAY) else CaseStatus.no_action
