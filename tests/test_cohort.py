"""A1 acceptance: cohort shape, truth consistency, note labels complete and exact, mock databases agree
with the golden fixtures.

Tests that need raw Synthea output skip when data/synthea/ is absent (it is gitignored); the ones that check
committed outputs (data/external, data/truth/labels.jsonl) always run.
"""
import json
import re
from functools import cache

import pytest

from engine import external, notes as notegen
from engine.config import LABELS_PATH, SYNTHEA_DIR
from engine.store import read_fixtures

needs_synthea = pytest.mark.skipif(not any(SYNTHEA_DIR.glob("*.json")) if SYNTHEA_DIR.exists() else True,
                                   reason="raw Synthea output not present (data/synthea is gitignored)")


@cache
def generated():
    from engine.cohort import load_cohort
    from engine.truth import generate

    patients = load_cohort()
    truths = {p.id: generate(p) for p in patients}
    notes, labels = {}, {}
    for p in patients:
        if not p.golden:
            notes[p.id], labels[p.id] = notegen.generate(p, truths[p.id])
    return patients, truths, notes, labels


@needs_synthea
def test_cohort_is_1000_adults_with_goldens():
    patients, *_ = generated()
    assert len(patients) == 1000
    assert {"g-rosa", "g-marcus", "g-deshawn", "g-linh", "g-karen", "g-omar", "g-bea"} <= {p.id for p in patients}
    assert all(19 <= p.age <= 64 for p in patients)
    assert len({p.id for p in patients}) == 1000


@needs_synthea
def test_spanish_share_near_quarter():
    patients, *_ = generated()
    assert 0.23 <= sum(p.language == "es" for p in patients) / len(patients) <= 0.27


@needs_synthea
def test_every_label_offset_matches_its_sentence():
    _, _, notes, labels = generated()
    all_notes = [n for ns in notes.values() for n in ns]
    notegen.check_labels(all_notes, [lb for lbs in labels.values() for lb in lbs])


@needs_synthea
def test_generation_is_deterministic():
    from engine.cohort import load_cohort
    from engine.truth import generate

    patients, truths, notes, _ = generated()
    again = {p.id: p for p in load_cohort()}
    for p in patients[:50]:
        assert again[p.id] == p
        assert generate(again[p.id]) == truths[p.id]
        if not p.golden:
            assert notegen.generate(again[p.id], generate(again[p.id]))[0] == notes[p.id]


# Words that name a qualifying condition or a functional limitation. Outside labeled spans, a note must not
# contain any of them; otherwise Channel B could find real evidence the labels do not know about.
EVIDENCE_WORDS = re.compile(
    r"\b(?:neuropath|kidney disease|dialysis|COPD|obstructive pulmonary|heart failure|coronary|ischemic|"
    r"chronic pain|back pain|fibromyalg|arthritis|osteoarthritis|substance use|opioid use|alcohol use disorder|"
    r"depressi(ve|on)(?! and alcohol use screening| screening)|anxiety|panic|seizure|epilep|brain injury|"
    r"cerebral palsy|intellectual disability|malignan|cancer(?! screening)|chemotherapy|HIV|asthma|migraine|"
    r"unable to|cannot stand|cannot walk|walker|wheelchair)", re.I)


@needs_synthea
def test_labels_are_complete_no_unlabeled_evidence():
    _, _, notes, labels = generated()
    for pid, ns in notes.items():
        spans = {}
        for lb in labels[pid]:
            spans.setdefault(lb.note_id, []).append((lb.start, lb.end))
        for n in ns:
            text = n.text
            for start, end in sorted(spans.get(n.id, []), reverse=True):
                text = text[:start] + " " * (end - start) + text[end:]
            hit = EVIDENCE_WORDS.search(text)
            assert hit is None, f"{n.id}: unlabeled evidence {hit.group(0)!r} in: {n.text}"


@needs_synthea
def test_truth_is_internally_consistent():
    patients, truths, notes, labels = generated()
    for p in patients:
        t = truths[p.id]
        assert t.exempt_or_compliant == bool(t.reasons)
        assert bool(t.facts["qualifying_condition"].value) == bool(t.frail_groups)
        assert bool(t.facts["significantly_impairs"].value) == any(g.impairs for g in t.frail_groups)
        if p.golden:
            continue
        pols = {lb.polarity for lb in labels[p.id]}
        documented = any(g.impairs and g.documentation == "positive" for g in t.frail_groups)
        frail_positive = any(lb.polarity == "positive" and lb.qualifying for lb in labels[p.id])
        assert documented == frail_positive, p.id
        if t.facts["significantly_impairs"].value:
            assert "function_normal" not in pols, p.id
            assert not any(lb.library_id == "distractor.0" for lb in labels[p.id]), p.id   # "Denies difficulty walking"


@needs_synthea
def test_golden_truth_tells_each_story():
    _, truths, _, _ = generated()
    rosa = truths["g-rosa"]
    assert rosa.facts["significantly_impairs"].value and rosa.frail_groups[0].documentation == "unstated"
    assert truths["g-marcus"].frail_groups[0].documentation == "positive"
    assert truths["g-deshawn"].reasons == ["school"]
    karen = truths["g-karen"]
    assert "medically_frail" not in karen.reasons and "hours" in karen.reasons and karen.wage_monthly is None
    assert truths["g-omar"].reasons == ["income"]
    assert truths["g-bea"].reasons == []


# --- committed outputs: always run ---------------------------------------------------------------

def test_external_dbs_hold_the_records_the_fixtures_cite():
    for case, _ in read_fixtures():
        for f in case.facts:
            if f.source.value == "external_db":
                rec = external.lookup(f.source_ref["db"], case.patient_id)
                assert rec is not None and rec["record_id"] == f.source_ref["record_id"], f.id
    assert external.lookup("student_enrollment", "g-deshawn")["enrollment_status"] == "half_time"
    assert external.lookup("state_wage_records", "g-omar")["monthly_income"] == 920
    assert external.lookup("state_wage_records", "g-karen") is None
    assert not external.lookup("county", "g-deshawn")["hardship"]


def test_golden_labels_in_labels_file_match_fixture_notes():
    notes = [n for _, ns in read_fixtures() for n in ns]
    rows = [json.loads(line) for line in LABELS_PATH.read_text().splitlines()]
    golden = [notegen.Label(**r) for r in rows if r["library_id"] == "golden"]
    assert len(golden) == len(notegen.GOLDEN_LABELS)
    notegen.check_labels(notes, golden)
    bait = next(lb for lb in golden if "mother" in lb.text)
    assert bait.polarity == "family" and not bait.qualifying
