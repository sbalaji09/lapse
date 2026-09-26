"""Live model tests: the evidence finder and verifier run against the real language model, with nothing replayed.

Every other test replays recorded model responses so it is free, fast and repeatable. These tests answer a
different question: does the live model still behave? They use an empty, throwaway cache (so every call reaches
the model and the recorded demo cache is never touched) and check:

  - the seven sample members still come out as the demo requires;
  - on a fixed sample of generated notes, found evidence still matches the labels we wrote.

Skipped unless LAPSE_LIVE=1, because they cost money (about $0.10 per run). Set LAPSE_LIVE_REPORT to a file path to
get a JSON summary (calls, cost, accuracy) - the Verification page does this.
"""
import json
import os
import random
import time

import pytest

from engine import channel_b, llm, verifier
from engine.channel_a import run_channel_a
from engine.cohort import golden_patients
from engine.config import LABELS_PATH, MODEL_FAST, MODEL_VERIFY
from engine.models import Tri
from engine.rulepack import load_pack
from engine.store import read_fixtures

pytestmark = pytest.mark.skipif(os.environ.get("LAPSE_LIVE") != "1",
                                reason="live model tests call the real model and cost money; set LAPSE_LIVE=1")

SAMPLE_NOTES = 30          # generated notes scored against labels
SAMPLE_SEED = 7
MIN_PRECISION, MIN_RECALL = 0.85, 0.75

REPORT: dict = {}


@pytest.fixture(scope="module", autouse=True)
def live_model(tmp_path_factory):
    """Fresh cache, network allowed, stats reset; write the report when the module finishes."""
    mp = pytest.MonkeyPatch()
    mp.setenv("LAPSE_LLM_CACHE_DIR", str(tmp_path_factory.mktemp("live-llm-cache")))
    mp.delenv("LAPSE_OFFLINE", raising=False)
    llm.stats.__init__()
    t0 = time.time()
    yield
    mp.undo()
    from engine.pipeline import _cost

    REPORT.update({
        "provider": llm._provider(),
        "models": {"extraction": llm._effective_model(MODEL_FAST), "verifier": llm._effective_model(MODEL_VERIFY)},
        "calls": llm.stats.misses,
        "replayed": llm.stats.hits,
        "cost_usd": round(sum(_cost(m, llm.stats.input_tokens[m], llm.stats.output_tokens.get(m, 0))
                              for m in llm.stats.input_tokens), 4),
        "seconds": round(time.time() - t0, 1),
    })
    path = os.environ.get("LAPSE_LIVE_REPORT")
    if path:
        with open(path, "w") as f:
            json.dump(REPORT, f, indent=1)


def _read(notes):
    """Extract and verify, live. Returns every located claim with the verifier's verdict applied."""
    calls = [channel_b.extraction_call(n) for n in notes]
    responses = llm.run_batch(calls)
    claims = []
    for note, resp in zip(notes, responses):
        claims += channel_b.to_claims(note, resp)[0]
    verdicts = llm.run_batch([verifier.verify_call(c) for c in claims])
    return [verifier.apply(c, v) for c, v in zip(claims, verdicts)]


@pytest.fixture(scope="module")
def golden():
    """The seven sample members, read live: {patient_id: (determination, facts, claims, notes)}."""
    pack = load_pack()
    patients = {p.id: p for p in golden_patients()}
    fixtures = {case.patient_id: notes for case, notes in read_fixtures()}
    claims = _read([n for ns in fixtures.values() for n in ns])
    out = {}
    for pid, notes in fixtures.items():
        mine = [c for c in claims if c.patient_id == pid]
        a_facts = run_channel_a(patients[pid], pack)[1]
        b_facts = channel_b.evidence_facts(mine, pack)
        out[pid] = (channel_b.determine(pid, a_facts, b_facts, pack), b_facts, mine, notes)
    REPORT["sample_members"] = {pid: v[0].status for pid, v in out.items()}
    return out


def test_every_quote_is_located_exactly_in_its_note(golden):
    for _, _, claims, notes in golden.values():
        texts = {n.id: n.text for n in notes}
        assert all(texts[c.note_id][c.start:c.end] == c.quote for c in claims)


def test_rosa_has_a_documented_condition_but_no_stated_limitation(golden):
    det, facts, _, _ = golden["g-rosa"]
    assert det.status == "not_determined"
    assert {f.key for f in facts} == {"qualifying_condition"}


def test_marcus_is_provable_from_his_notes(golden):
    assert golden["g-marcus"][0].rule_ids == ["medically_frail"]


def test_linh_is_supported_by_her_notes(golden):
    _, facts, _, _ = golden["g-linh"]
    assert {"qualifying_condition", "significantly_impairs"} <= {f.key for f in facts}


def test_karen_has_no_verified_limitation(golden):
    assert not any(c.verified and c.significantly_impairs == Tri.true for c in golden["g-karen"][2])


def test_bea_mothers_arthritis_never_counts(golden):
    det, _, claims, _ = golden["g-bea"]
    assert not any(c.verified and "mother" in c.quote.lower() for c in claims)
    assert det.status == "not_determined"


def test_evidence_on_sample_notes_matches_the_labels():
    from engine import store
    from engine.eval import POSITIVE_CONDITION, match
    from engine.notes import Label

    notes = [n for n in store.all_notes() if not n.patient_id.startswith("g-")]
    if not notes:
        pytest.skip("no generated notes in the store; run the pipeline first")
    labels = [Label(**json.loads(line)) for line in LABELS_PATH.read_text().splitlines()]
    labelled = sorted({lb.note_id for lb in labels})
    sample_ids = set(random.Random(SAMPLE_SEED).sample(labelled, min(SAMPLE_NOTES, len(labelled))))
    sample = [n for n in notes if n.id in sample_ids]
    sample_labels = [lb for lb in labels if lb.note_id in sample_ids]

    claims = _read(sample)
    wanted = [lb for lb in sample_labels if lb.qualifying and lb.polarity in POSITIVE_CONDITION]
    found = [c for c in claims if c.verified and c.qualifying_category and c.category != "sud_treatment"]
    matched = match(found, wanted)
    tp = sum(v is not None for v in matched.values())
    precision = tp / len(found) if found else 0.0
    recall = tp / len(wanted) if wanted else 0.0

    negatives = [lb for lb in sample_labels if not lb.qualifying and lb.polarity in
                 ("negated", "resolved", "family", "past", "hypothetical")]
    counted_negatives = sum(v is not None for v in match([c for c in claims if c.verified], negatives).values())

    REPORT["sample_notes"] = {"notes": len(sample), "evidence_labels": len(wanted), "found": len(found),
                              "precision": round(precision, 3), "recall": round(recall, 3),
                              "distractors": len(negatives), "distractors_counted": counted_negatives,
                              "dropped_by_verifier": sum(c.verified is False for c in claims)}
    assert precision >= MIN_PRECISION, f"precision {precision:.3f} below {MIN_PRECISION}"
    assert recall >= MIN_RECALL, f"recall {recall:.3f} below {MIN_RECALL}"
    assert counted_negatives == 0, f"{counted_negatives} negated/family/past sentences were counted as evidence"
