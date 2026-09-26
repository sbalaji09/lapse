"""A4 acceptance: buckets and the solver reproduce the golden fixtures from their facts alone, and the live
demo flows (Rosa replies, the clinician signs, Deshawn's database click) move cases the way PROJECT.md says."""
from datetime import datetime

import pytest

from engine import buckets
from engine.models import Bucket, Fact, Holder, Source
from engine.rulepack import load_pack
from engine.solver import default_status, reevaluate, resolve_database, settle
from engine.store import read_fixtures

KEEP = {"g-deshawn": ("enrolled_half_time_school",)}


@pytest.fixture
def golden():
    return {c.patient_id: c for c, _ in read_fixtures()}


def settled(case):
    return settle(case, keep_open=KEEP.get(case.patient_id, ()))[0]


@pytest.mark.parametrize("pid,bucket,fragile,top", [
    ("g-rosa", Bucket.ONE_AWAY, False, ("standing_tolerance_minutes", Holder.patient)),
    ("g-marcus", Bucket.PROVABLE, False, ("limitation_attested", Holder.clinician)),
    ("g-deshawn", Bucket.ONE_AWAY, False, ("enrolled_half_time_school", Holder.database)),
    ("g-linh", Bucket.SAFE, False, None),
    ("g-karen", Bucket.SAFE, True, None),
    ("g-omar", Bucket.SAFE, False, None),
    ("g-bea", Bucket.NO_PATH, False, None),
])
def test_golden_buckets_from_facts_alone(golden, pid, bucket, fragile, top):
    case = settled(golden[pid])
    assert case.bucket == bucket
    assert buckets.is_fragile(case.determination_a, case.claims + case.dropped_claims) == fragile
    assert ((case.missing[0].key, case.missing[0].holder) if case.missing else None) == top


def test_rosa_questions_and_follow_up(golden):
    rosa = settled(golden["g-rosa"])
    assert [m.key for m in rosa.missing] == ["standing_tolerance_minutes", "limitation_attested"]
    assert rosa.missing[0].question["es"].startswith("¿Cuánto tiempo")
    assert "neuropathy" in rosa.missing[1].question["en"] and "Rosa" in rosa.missing[1].question["en"]
    assert rosa.missing[0].id == "m-rosa-1"                       # fixture ids survive re-evaluation


def test_nobody_is_asked_what_a_database_could_answer(golden):
    rosa = settled(golden["g-rosa"])
    looked_up = {f.source_ref["db"] for f in rosa.facts if f.source == Source.external_db}
    assert {"student_enrollment", "va", "county"} <= looked_up
    assert not any(m.holder == Holder.database and m.status == "open" for m in rosa.missing)


def _reply(case, key, value, quote):
    case.facts.append(Fact(id=f"fr-{key}", patient_id=case.patient_id, key=key, value=value,
                           source=Source.patient_reply, source_ref={"message_id": "msg-1"}, quote=quote,
                           recorded_at=datetime(2026, 9, 26, 12, 0), rule_pack_version="ca-2027.02-demo"))


def test_rosa_reply_flips_case_to_clinician(golden):
    rosa = settled(golden["g-rosa"])
    _reply(rosa, "standing_tolerance_minutes", 10, "diez minutos")
    rosa.missing[0].status = "resolved_true"                      # what Track B's inbound handler does
    rosa = reevaluate(rosa)
    assert rosa.bucket == Bucket.PROVABLE
    assert rosa.determination_final.status == "exempt" and rosa.determination_final.rule_ids == ["medically_frail"]
    open_items = [m for m in rosa.missing if m.status == "open"]
    assert [(m.key, m.holder) for m in open_items] == [("limitation_attested", Holder.clinician)]
    assert [m.status for m in rosa.missing if m.key == "standing_tolerance_minutes"] == ["resolved_true"]


def test_long_standing_tolerance_does_not_exempt(golden):
    rosa = settled(golden["g-rosa"])
    _reply(rosa, "standing_tolerance_minutes", 120, "dos horas")
    rosa = reevaluate(rosa)
    assert rosa.determination_final.status == "not_determined"
    assert rosa.bucket == Bucket.NO_PATH                          # impairment now known false: no lead left


def test_clinician_signature_closes_the_case(golden):
    marcus = settled(golden["g-marcus"])
    marcus.facts.append(Fact(id="fc-1", patient_id="g-marcus", key="limitation_attested", value=True,
                             source=Source.clinician_attestation, source_ref={"attestation_id": "att-1"},
                             recorded_at=datetime(2026, 9, 26), rule_pack_version="ca-2027.02-demo"))
    marcus.missing[0].status = "resolved_true"
    marcus = reevaluate(marcus)
    assert marcus.bucket == Bucket.PROVABLE
    assert not [m for m in marcus.missing if m.status == "open"]


def test_deshawn_database_click_clears_him_without_contact(golden):
    deshawn = settled(golden["g-deshawn"])
    target = deshawn.missing[0]
    fact = resolve_database(deshawn, target)
    assert fact.value is True and fact.source_ref["db"] == "student_enrollment" and fact.source_ref["record_id"]
    deshawn.facts.append(fact)
    target.status = "resolved_true"
    deshawn = reevaluate(deshawn)
    assert deshawn.bucket == Bucket.SAFE
    assert deshawn.determination_final.rule_ids == ["school"]
    assert buckets.cleared_by_records(deshawn.determination_final, deshawn.facts, load_pack())


def test_default_status(golden):
    assert default_status(settled(golden["g-rosa"])).value == "needs_action"
    assert default_status(settled(golden["g-omar"])).value == "no_action"


def test_functional_fact_derives_impairment():
    base = dict(patient_id="p", source=Source.patient_reply, source_ref={}, recorded_at=datetime(2026, 1, 1),
                rule_pack_version="v")
    assert buckets.values([Fact(id="a", key="standing_tolerance_minutes", value=10, **base)])["significantly_impairs"] is True
    assert buckets.values([Fact(id="a", key="standing_tolerance_minutes", value=45, **base)])["significantly_impairs"] is False
    explicit = [Fact(id="a", key="standing_tolerance_minutes", value=10, **base),
                Fact(id="b", key="significantly_impairs", value=False, **base)]
    assert buckets.values(explicit)["significantly_impairs"] is False     # an explicit answer wins


def test_later_fact_wins_regardless_of_timestamp():
    base = dict(patient_id="p", key="hours_per_month", source=Source.patient_reply, source_ref={},
                rule_pack_version="v")
    facts = [Fact(id="a", value=0, recorded_at=datetime(2027, 2, 15), **base),
             Fact(id="b", value=90, recorded_at=datetime(2026, 9, 26), **base)]
    assert buckets.values(facts)["hours_per_month"] == 90
