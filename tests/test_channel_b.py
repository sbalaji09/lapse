"""A3 acceptance: offsets are computed in code and always exact, the verifier sees only claim + span, only
verified claims become facts, and the golden patients come out as contracted.

No test here touches the network. The golden end-to-end test replays cached LLM responses with
LAPSE_OFFLINE=1 and skips when the cache is absent (.cache/ is gitignored).
"""
import pytest

from engine import channel_b, llm, verifier
from engine.cohort import golden_patients
from engine.channel_a import run_channel_a
from engine.models import Note, Tri
from engine.rulepack import load_pack
from engine.store import read_fixtures

NOTE = Note(id="n-x-1", patient_id="p-x", date="2026-05-01", author="A, MD",
            text="S: Reports pain.  Cannot walk more than\none block.\nA: Severe COPD, stable.")


def raw(quote, impairs="unknown", category="serious_complex_medical", qualifying=True):
    return {"category": category, "condition": "COPD", "qualifying_category": qualifying,
            "significantly_impairs": impairs, "quote": quote}


def test_locate_exact_then_whitespace_normalized():
    assert channel_b.locate(NOTE.text, "Severe COPD, stable.") == (NOTE.text.index("Severe"), len(NOTE.text))
    start, end = channel_b.locate(NOTE.text, "Cannot walk more than one block.")
    assert NOTE.text[start:end] == "Cannot walk more than\none block."
    assert channel_b.locate(NOTE.text, "Cannot run a marathon.") is None
    assert channel_b.locate(NOTE.text, "   ") is None


def test_to_claims_offsets_always_match_and_unlocatable_are_counted():
    resp = {"claims": [raw("Severe COPD, stable."), raw("Cannot walk more than one block.", "true"),
                       raw("Made-up sentence the model invented."), raw("Severe COPD, stable.")]}
    claims, unlocatable = channel_b.to_claims(NOTE, resp)
    assert unlocatable == 1
    assert len(claims) == 2                                    # duplicate collapsed
    for c in claims:
        assert NOTE.text[c.start:c.end] == c.quote
    assert claims[1].quote == "Cannot walk more than\none block."   # quote is the note's text, not the model's


def test_verifier_sees_only_the_claim_and_the_span():
    [c], _ = channel_b.to_claims(NOTE, {"claims": [raw("Cannot walk more than one block.", "true")]})
    call = verifier.verify_call(c)
    assert "Reports pain" not in call["user"] and "Severe COPD" not in call["user"]
    assert c.quote in call["user"] and call["model"] != channel_b.MODEL_FAST


def test_only_verified_claims_become_positive_facts():
    pack = load_pack()
    claims, _ = channel_b.to_claims(NOTE, {"claims": [raw("Severe COPD, stable."),
                                                      raw("Cannot walk more than one block.", "true")]})
    unverified = channel_b.evidence_facts(claims, pack)
    assert unverified == []
    rejected_limit = [verifier.apply(claims[0], {"supported": True, "reason": "ok"}),
                      verifier.apply(claims[1], {"supported": False, "reason": "no"})]
    facts = channel_b.evidence_facts(rejected_limit, pack)
    assert [(f.key, f.value) for f in facts] == [("qualifying_condition", True)]
    both = [verifier.apply(c, {"supported": True, "reason": "ok"}) for c in claims]
    facts = channel_b.evidence_facts(both, pack)
    assert {f.key for f in facts} == {"qualifying_condition", "significantly_impairs"}
    for f in facts:
        assert NOTE.text[f.source_ref["start"]:f.source_ref["end"]] == f.quote
    assert all(f.value is True for f in facts)                 # notes only ever add positive facts


def test_sud_treatment_claims_evidence_the_separate_exemption():
    pack = load_pack()
    note = Note(id="n-y-1", patient_id="p-y", date="2026-05-01", author="A, MD",
                text="S: Attending an intensive outpatient program for alcohol use disorder three days a week.")
    claims, _ = channel_b.to_claims(note, {"claims": [raw(note.text[3:], category="sud_treatment", qualifying=False)]})
    facts = channel_b.evidence_facts([verifier.apply(claims[0], {"supported": True, "reason": "ok"})], pack)
    assert [f.key for f in facts] == ["in_sud_treatment"]
    det = channel_b.determine("p-y", [], facts, pack)
    assert det.status == "exempt" and det.rule_ids == ["sud_treatment"]


def _golden_cached() -> bool:
    notes = [n for _, ns in read_fixtures() for n in ns]
    return not llm.uncached([channel_b.extraction_call(n) for n in notes])


@pytest.mark.skipif(not _golden_cached(), reason="golden LLM responses not cached; run the channel_b stage once")
def test_golden_patients_replay_offline(monkeypatch):
    monkeypatch.setenv("LAPSE_OFFLINE", "1")
    pack = load_pack()
    patients = {p.id: p for p in golden_patients()}
    out = {}
    for case, notes in read_fixtures():
        claims = []
        for n in notes:
            claims += channel_b.to_claims(n, llm.call_json(**channel_b.extraction_call(n)))[0]
        claims = [verifier.apply(c, llm.call_json(**verifier.verify_call(c))) for c in claims]
        a_facts = run_channel_a(patients[case.patient_id], pack)[1]
        b_facts = channel_b.evidence_facts(claims, pack)
        out[case.patient_id] = (channel_b.determine(case.patient_id, a_facts, b_facts, pack), b_facts, claims)

    rosa_det, rosa_facts, _ = out["g-rosa"]
    assert rosa_det.status == "not_determined"
    assert {f.key for f in rosa_facts} == {"qualifying_condition"}      # qualifying true, impairs unknown
    assert out["g-marcus"][0].rule_ids == ["medically_frail"]            # provable from the notes alone
    assert out["g-linh"][0].status == "exempt"
    karen_claims = out["g-karen"][2]
    assert not any(c.verified and c.significantly_impairs == Tri.true for c in karen_claims)   # fragile
    assert not any(c.verified and "mother" in c.quote for c in out["g-bea"][2])               # bait never counts
    assert out["g-bea"][0].status == "not_determined"
