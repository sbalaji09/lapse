"""T0a acceptance: golden fixtures load, every span checks out, and each hero patient matches its contract."""
import json
import re

import pytest

from engine import golden, store
from engine.checks import IntegrityError, check_case
from engine.config import FIXTURES_PATH
from engine.models import Bucket, Holder


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "test.sqlite"))
    store.load_fixtures()


def test_fixture_file_matches_builder():
    # golden_cases.json is generated; a hand edit that drifts from engine/golden.py fails here.
    assert json.loads(FIXTURES_PATH.read_text()) == golden.build()


def test_all_seven_load_sorted_by_renewal():
    cases = store.list_cases()
    assert [c.patient_id for c in cases] == ["g-marcus", "g-rosa", "g-deshawn", "g-bea", "g-linh", "g-karen", "g-omar"]


def test_every_claim_quote_matches_its_note():
    for case in store.list_cases():
        notes = {n.id: n for n in store.get_notes(case.patient_id)}
        for c in case.claims + case.dropped_claims:
            assert notes[c.note_id].text[c.start:c.end] == c.quote


def test_notes_are_progress_note_length():
    for case in store.list_cases():
        for n in store.get_notes(case.patient_id):
            assert 120 <= len(n.text.split()) <= 250, n.id


@pytest.mark.parametrize("pid,bucket,fragile,top_key,holder", [
    ("g-rosa", Bucket.ONE_AWAY, False, "standing_tolerance_minutes", Holder.patient),
    ("g-marcus", Bucket.PROVABLE, False, "limitation_attested", Holder.clinician),
    ("g-deshawn", Bucket.ONE_AWAY, False, "enrolled_half_time_school", Holder.database),
    ("g-linh", Bucket.SAFE, False, None, None),
    ("g-karen", Bucket.SAFE, True, None, None),
    ("g-omar", Bucket.SAFE, False, None, None),
    ("g-bea", Bucket.NO_PATH, False, None, None),
])
def test_golden_contract(pid, bucket, fragile, top_key, holder):
    case = store.get_case(pid)
    assert (case.bucket, case.fragile) == (bucket, fragile)
    if top_key is None:
        assert case.missing == []
    else:
        assert (case.missing[0].key, case.missing[0].holder) == (top_key, holder)


def test_rosa_story():
    rosa = store.get_case("g-rosa")
    assert rosa.renewal_date.isoformat() == "2027-03-04"
    assert len(store.get_notes("g-rosa")) == 3
    # The state never sees neuropathy as a primary diagnosis.
    assert all("neuropathy" not in d["display"].lower() for d in rosa.billed_dx_12mo if d["sequence"] == 1)
    # Qualifying condition is in the notes, function is not: nothing about standing or work.
    for n in store.get_notes("g-rosa"):
        assert not re.search(r"\bstand|\bwork|\bwalk", n.text, re.I), n.id
    assert {"en", "es"} <= rosa.missing[0].question.keys()
    assert rosa.missing[1].key == "limitation_attested"


def test_marcus_has_three_verified_claims_for_the_card():
    marcus = store.get_case("g-marcus")
    assert len(marcus.claims) == 3 and all(c.verified for c in marcus.claims)


def test_bea_bait_is_dropped_by_verifier():
    bea = store.get_case("g-bea")
    assert bea.claims == []
    [bait] = bea.dropped_claims
    assert bait.verified is False and "mother" in bait.quote and bait.verifier_reason


def test_omar_is_compliant_on_income_and_never_contacted():
    omar = store.get_case("g-omar")
    assert omar.determination_a.status == "compliant" and omar.determination_a.rule_ids == ["income"]
    assert all(e["kind"] == "pipeline_run" for e in omar.events)


def test_state_exemptions_cite_a_real_primary_claim():
    for pid in ("g-linh", "g-karen"):
        case = store.get_case(pid)
        billed = {(d["claim_id"], d["code"], d["sequence"]) for d in case.billed_dx_12mo}
        cited = [f for f in case.facts if f.id in case.determination_a.fact_ids and f.source.value == "billing_code"]
        assert cited and all((f.source_ref["claim_id"], f.source_ref["code"], 1) in billed for f in cited)


def test_renewals_within_60_days():
    from engine.config import AS_OF_DATE
    for case in store.list_cases():
        assert 0 < (case.renewal_date - AS_OF_DATE).days <= 60


def test_window_filter():
    assert [c.patient_id for c in store.list_cases(window_days=30)] == ["g-marcus", "g-rosa", "g-deshawn", "g-bea"]
    assert [c.patient_id for c in store.list_cases(fragile=True)] == ["g-karen"]


def test_check_case_rejects_a_shifted_span():
    case = store.get_case("g-rosa")
    notes = store.get_notes("g-rosa")
    case.claims[0].start += 1
    with pytest.raises(IntegrityError):
        check_case(case, notes)


def test_check_case_rejects_unregistered_fact_key():
    case = store.get_case("g-omar")
    case.facts[0].key = "not_a_registry_key"
    with pytest.raises(IntegrityError):
        check_case(case, store.get_notes("g-omar"))
