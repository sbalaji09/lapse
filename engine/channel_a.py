"""Channel A: the State Simulator. A faithful, deterministic, zero-LLM copy of the state's ex parte check.

It reads only what the rule pack lists in state_visible_sources: billing codes, the structured member
record, and the state's own databases. Never notes, never patient replies, never the databases Lapse can
query but the state does not. For medical frailty it reads the PRIMARY diagnosis on each claim in the
lookback window against the state code list and treats a hit as meeting both frailty tests. Nothing else
counts. That is the honest baseline: every person Channel A misses is someone the state would miss.

Each rule's `state_ex_parte` block in the pack names the method below that checks it.
"""
from collections.abc import Callable
from datetime import date, timedelta

from engine import external
from engine.cohort import Patient
from engine.config import AS_OF_DATE, PIPELINE_RUN_AT
from engine.models import Determination, Fact, Source
from engine.rulepack import RulePack, load_pack

Method = Callable[[Patient, dict, RulePack], list[Fact]]


def months_before(day: date, months: int) -> date:
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(y, m + 1, min(day.day, 28))


def _fact(p: Patient, pack: RulePack, key: str, value, source: Source, ref: dict) -> Fact:
    return Fact(id=f"fa-{p.id}-{key}", patient_id=p.id, key=key, value=value, source=source, source_ref=ref,
                recorded_at=PIPELINE_RUN_AT, rule_pack_version=pack.version)


def primary_dx_only(p: Patient, spec: dict, pack: RulePack) -> list[Fact]:
    """A listed code as the PRIMARY diagnosis on any claim in the lookback window meets both frailty tests."""
    codes = pack.code_list(spec["code_list"])
    start = months_before(AS_OF_DATE, pack.lookback_months)
    hits = [(c, d) for c in p.claims if start <= c.date <= AS_OF_DATE
            for d in c.diagnoses if d.sequence == 1 and d.code in codes]
    if not hits:
        return []
    claim, dx = max(hits, key=lambda h: (h[0].date, h[0].id))
    ref = {"claim_id": claim.id, "code": dx.code, "display": dx.display, "sequence": 1,
           "date": claim.date.isoformat()}
    return [_fact(p, pack, "qualifying_condition", True, Source.billing_code, ref),
            _fact(p, pack, "significantly_impairs", True, Source.billing_code, ref)]


def dx_any_sequence(p: Patient, spec: dict, pack: RulePack) -> list[Fact]:
    codes = pack.code_list(spec["code_list"])
    start = AS_OF_DATE - timedelta(days=spec["lookback_days"])
    hits = [(c, d) for c in p.claims if start <= c.date <= AS_OF_DATE for d in c.diagnoses if d.code in codes]
    if hits:
        claim, dx = max(hits, key=lambda h: (h[0].date, h[0].id))
        return [_fact(p, pack, spec["fact"], True, Source.billing_code,
                      {"claim_id": claim.id, "code": dx.code, "display": dx.display, "sequence": dx.sequence,
                       "date": claim.date.isoformat()})]
    if spec.get("false_if_sex") == p.sex:
        return [_fact(p, pack, spec["fact"], False, Source.structured_record, {"resource": "Patient", "field": "gender"})]
    return []


def structured_record(p: Patient, spec: dict, pack: RulePack) -> list[Fact]:
    fields = {"race_ai_an": (p.ai_an, "race")}
    value, field = fields[spec["field"]]
    return [_fact(p, pack, spec["fact"], value, Source.structured_record, {"resource": "Patient", "field": field})]


def age_limit(p: Patient, spec: dict, pack: RulePack) -> list[Fact]:
    if p.age > spec["max_age"]:
        return [_fact(p, pack, spec["fact"], False, Source.structured_record,
                      {"resource": "Patient", "field": "birthDate"})]
    return []


def database(p: Patient, spec: dict, pack: RulePack) -> list[Fact]:
    """A field from one of the state's own databases. `no_record` says what absence means:
    missing key -> unknown (no fact); false -> known false; none -> known absent (value None)."""
    db = spec["db"]
    rec = external.lookup(db, p.id)
    if rec is None:
        if "no_record" not in spec:
            return []
        value = None if spec["no_record"] == "none" else spec["no_record"]
        return [_fact(p, pack, spec["fact"], value, Source.external_db, {"db": db, "record_id": None})]
    value = rec[spec["field"]]
    if spec.get("transform") == "days_since":
        value = (AS_OF_DATE - date.fromisoformat(value)).days
    return [_fact(p, pack, spec["fact"], value, Source.external_db, {"db": db, "record_id": rec["record_id"]})]


METHODS: dict[str, tuple[Method, str | None]] = {
    # method -> (implementation, source it reads; None = named by the spec's `db`)
    "primary_dx_only": (primary_dx_only, "billing_code"),
    "dx_any_sequence": (dx_any_sequence, "billing_code"),
    "structured_record": (structured_record, "structured_record"),
    "age_limit": (age_limit, "structured_record"),
    "database": (database, None),
}


def run_channel_a(p: Patient, pack: RulePack | None = None) -> tuple[Determination, list[Fact]]:
    pack = pack or load_pack()
    facts: list[Fact] = []
    for rule in pack.rules:
        spec = rule.state_ex_parte
        if not spec:
            continue
        method, source = METHODS[spec["method"]]
        source = source or spec["db"]
        if source not in pack.state_visible_sources:
            raise PermissionError(f"{pack.state}/{rule.id}: the state cannot read {source!r}")
        facts += method(p, spec, pack)
    status, rule_ids = pack.determine({f.key: f.value for f in facts})
    det = Determination(patient_id=p.id, channel="A", status=status, rule_ids=rule_ids,
                        fact_ids=[f.id for f in facts], rule_pack_version=pack.version)
    return det, facts
