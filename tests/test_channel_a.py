"""A2 acceptance: the rule-pack evaluator's three-valued logic, and a faithful state simulator."""
import dataclasses
import time
from datetime import date

import pytest

from engine.channel_a import months_before, run_channel_a
from engine.cohort import golden_patients
from engine.models import Tri
from engine.rulepack import load_pack, parse_term
from tests.test_cohort import needs_synthea

T, F, U = Tri.true, Tri.false, Tri.unknown


@pytest.fixture(scope="module")
def pack():
    return load_pack("ca")


@pytest.fixture(scope="module")
def golden():
    return {p.id: p for p in golden_patients()}


# --- evaluator ------------------------------------------------------------------------------------

def test_atoms_are_three_valued():
    t = parse_term("hours_per_month >= 80")
    assert t.evaluate({}) == U
    assert t.evaluate({"hours_per_month": Tri.unknown}) == U
    assert t.evaluate({"hours_per_month": 80}) == T
    assert t.evaluate({"hours_per_month": 79}) == F
    assert parse_term("ai_an").evaluate({"ai_an": False}) == F


def test_known_absent_value_is_false_not_unknown():
    assert parse_term("released_incarceration_days <= 90").evaluate({"released_incarceration_days": None}) == F


def test_or_term_needs_one_true_or_all_false():
    t = parse_term("dependent_child_13_or_under | caregiver_disabled_person")
    assert t.evaluate({"caregiver_disabled_person": True}) == T
    assert t.evaluate({"dependent_child_13_or_under": False}) == U
    assert t.evaluate({"dependent_child_13_or_under": False, "caregiver_disabled_person": False}) == F


def test_rule_and_across_terms(pack):
    frail = pack.rule("medically_frail")
    assert frail.evaluate({"qualifying_condition": True}) == U
    assert [str(t) for t in frail.unknown_terms({"qualifying_condition": True})] == ["significantly_impairs"]
    assert frail.evaluate({"qualifying_condition": True, "significantly_impairs": False}) == F
    assert frail.evaluate({"qualifying_condition": True, "significantly_impairs": True}) == T


def test_type_confusion_is_an_error_not_a_guess():
    with pytest.raises(TypeError):
        parse_term("ai_an").evaluate({"ai_an": 3})
    with pytest.raises(TypeError):
        parse_term("hours_per_month >= 80").evaluate({"hours_per_month": True})


def test_compliance_is_checked_before_exemptions(pack):
    status, rules = pack.determine({"monthly_income": 900, "ai_an": True})
    assert status == "compliant" and rules == ["income", "ai_an"]


def test_bad_rule_text_is_rejected():
    with pytest.raises(ValueError):
        parse_term("hours_per_month >> 80")


# --- state simulator on the golden patients --------------------------------------------------------

@pytest.mark.parametrize("pid,status,rules", [
    ("g-linh", "exempt", ["medically_frail"]),       # CKD 4 billed as primary
    ("g-karen", "exempt", ["medically_frail"]),      # MDD billed as primary
    ("g-omar", "compliant", ["income"]),             # $920/month in wage records
    ("g-rosa", "not_determined", []),                # neuropathy only as a secondary code
    ("g-marcus", "not_determined", []),              # COPD only as a secondary code
    ("g-deshawn", "not_determined", []),             # student enrollment is not state-visible
    ("g-bea", "not_determined", []),
])
def test_golden_determinations(golden, pack, pid, status, rules):
    det, _ = run_channel_a(golden[pid], pack)
    assert (det.status, det.rule_ids) == (status, rules)


def test_frailty_exemption_cites_the_primary_claim(golden, pack):
    det, facts = run_channel_a(golden["g-linh"], pack)
    billing = [f for f in facts if f.source.value == "billing_code"]
    assert {f.key for f in billing} == {"qualifying_condition", "significantly_impairs"}
    claim_ids = {c.id for c in golden["g-linh"].claims}
    assert all(f.source_ref["claim_id"] in claim_ids and f.source_ref["sequence"] == 1 for f in billing)
    assert set(det.fact_ids) == {f.id for f in facts}


def test_secondary_codes_never_count(golden, pack):
    marcus = golden["g-marcus"]
    assert any(d.code == "185086009" and d.sequence == 2 for c in marcus.claims for d in c.diagnoses)
    assert "185086009" in pack.code_list("codes/ca_frailty.txt")
    _, facts = run_channel_a(marcus, pack)
    assert not any(f.key == "qualifying_condition" for f in facts)


def test_primary_code_outside_lookback_does_not_count(golden, pack):
    linh = golden["g-linh"].model_copy(deep=True)
    old = months_before(date(2027, 2, 15), 13)
    for c in linh.claims:
        c.date = old
    det, _ = run_channel_a(linh, pack)
    assert det.status == "not_determined"


def test_state_cannot_read_sources_it_does_not_hold(golden, pack):
    blind = dataclasses.replace(pack, state_visible_sources=pack.state_visible_sources - {"snap"})
    with pytest.raises(PermissionError):
        run_channel_a(golden["g-bea"], blind)


def test_unknown_stays_unknown(golden, pack):
    _, facts = run_channel_a(golden["g-rosa"], pack)
    keys = {f.key for f in facts}
    # No wage record, and nothing the state holds speaks to hours, school, children or frailty.
    assert keys.isdisjoint({"monthly_income", "hours_per_month", "enrolled_half_time_school",
                            "dependent_child_13_or_under", "qualifying_condition", "significantly_impairs"})


# --- full cohort --------------------------------------------------------------------------------------

@needs_synthea
def test_full_cohort_fast_and_every_frailty_exemption_backed_by_a_real_primary_claim(pack):
    from engine.cohort import load_cohort

    patients = load_cohort()
    codes = pack.code_list("codes/ca_frailty.txt")
    start = months_before(date(2027, 2, 15), pack.lookback_months)
    t0 = time.time()
    runs = [run_channel_a(p, pack) for p in patients]
    assert time.time() - t0 < 5
    by_id = {p.id: p for p in patients}
    frail = 0
    for det, facts in runs:
        if "medically_frail" not in det.rule_ids:
            continue
        frail += 1
        ref = next(f.source_ref for f in facts if f.key == "qualifying_condition")
        claim = next(c for c in by_id[det.patient_id].claims if c.id == ref["claim_id"])
        assert start <= claim.date and ref["code"] in codes
        assert any(d.code == ref["code"] and d.sequence == 1 for d in claim.diagnoses)
    assert frail > 0
