"""Final determination and buckets: SAFE / PROVABLE / ONE_AWAY / NO_PATH, plus the fragile flag. Zero LLM.

Every fact from every source (state data, verified note spans, databases Lapse looked up, the patient's own
words, the clinician's attestation) is merged and evaluated with the same three-valued rule-pack evaluator the
state simulator uses. The bucket then says what, if anything, a person has to do:

  SAFE      the state already clears them, or a database Lapse checked does - nobody needs to act.
            Doing nothing is the cheapest correct action.
  PROVABLE  the state does not clear them, but evidence already in the chart does: a clinician signs.
  ONE_AWAY  one missing fact, backed by a lead, would clear them (engine/solver.py). This is the product.
  NO_PATH   nothing within one fact; they need help reporting hours (out of scope, just shown).
  fragile   SAFE only because the state read a frailty billing code, and not one verified sentence in the
            chart supports an impairment. Audit exposure for the plan.
"""
from functools import cache

import yaml

from engine.config import FACT_REGISTRY_PATH
from engine.models import Bucket, Claim, Determination, Fact, Source, Tri
from engine.rulepack import Atom, Rule, RulePack

# Facts no person had to produce: the state's own data and databases Lapse looked up.
RECORD_SOURCES = {Source.billing_code, Source.structured_record, Source.external_db}


@cache
def _impairs_if() -> dict[str, Atom]:
    """Registry facts that imply significantly_impairs, e.g. standing_tolerance_minutes < 30."""
    out = {}
    for key, spec in yaml.safe_load(FACT_REGISTRY_PATH.read_text()).items():
        if "impairs_if" in spec:
            op, number = spec["impairs_if"].split()
            out[key] = Atom(key, op, float(number))
    return out


def current_facts(facts: list[Fact]) -> dict[str, Fact]:
    """The fact that currently stands for each key: the last one appended. Case.facts is append-only, so list
    order is the true order; timestamps are not comparable (the batch run is stamped AS_OF_DATE, live loop
    actions with the wall clock)."""
    return {f.key: f for f in facts}


def values(facts: list[Fact]) -> dict:
    """Evaluator input. A functional fact (standing tolerance) settles significantly_impairs when nothing else has."""
    vals = {k: f.value for k, f in current_facts(facts).items()}
    if "significantly_impairs" not in vals:
        for key, atom in _impairs_if().items():
            if key in vals:
                result = atom.evaluate(vals)
                if result != Tri.unknown:
                    vals["significantly_impairs"] = result == Tri.true
                    break
    return vals


def final_determination(patient_id: str, facts: list[Fact], pack: RulePack) -> Determination:
    status, rule_ids = pack.determine(values(facts))
    return Determination(patient_id=patient_id, channel="final", status=status, rule_ids=rule_ids,
                         fact_ids=[f.id for f in facts], rule_pack_version=pack.version)


def supporting_facts(rule: Rule, facts: list[Fact]) -> list[Fact]:
    """The facts that make a satisfied rule true (for an OR term, the atoms that are true)."""
    vals = values(facts)
    current = current_facts(facts)
    out = []
    for term in rule.terms:
        for atom in term.atoms:
            if atom.evaluate(vals) == Tri.true:
                if atom.key in current:
                    out.append(current[atom.key])
                elif atom.key == "significantly_impairs":      # derived from a functional fact
                    out += [current[k] for k in _impairs_if() if k in current]
    return out


def cleared_by_records(det: Determination, facts: list[Fact], pack: RulePack) -> bool:
    """True when some satisfied rule rests only on records - nobody had to say or sign anything."""
    return any(all(f.source in RECORD_SOURCES for f in supporting_facts(pack.rule(r), facts))
               for r in det.rule_ids)


def is_fragile(det_a: Determination, claims: list[Claim]) -> bool:
    """State clears them only through a frailty code, and no verified sentence shows an impairment."""
    only_frailty = det_a.status == "exempt" and det_a.rule_ids == ["medically_frail"]
    supported = any(c.verified and c.qualifying_category and c.significantly_impairs == Tri.true for c in claims)
    return only_frailty and not supported


def bucket(det_a: Determination, det_final: Determination, facts: list[Fact], has_lead: bool,
           pack: RulePack) -> Bucket:
    if det_a.status != "not_determined":
        return Bucket.SAFE
    if det_final.status != "not_determined":
        return Bucket.SAFE if cleared_by_records(det_final, facts, pack) else Bucket.PROVABLE
    return Bucket.ONE_AWAY if has_lead else Bucket.NO_PATH
