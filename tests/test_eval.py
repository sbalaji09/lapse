"""A5: metric arithmetic, claim/label matching, simulated outreach, and the served eval.json contract."""
import json

from engine import eval as ev
from engine.config import DATA_DIR
from engine.models import Bucket, Claim, Tri
from engine.notes import Label
from engine.solver import settle
from engine.store import read_fixtures
from engine.truth import load_truth


def test_prf_and_confusion():
    assert ev.prf(8, 2, 2) == {"p": 0.8, "r": 0.8, "f1": 0.8}
    assert ev.prf(0, 0, 5) == {"p": 0.0, "r": 0.0, "f1": 0.0}
    assert ev.confusion({"a": True, "b": False, "c": True}, {"a": True, "b": True, "c": False}) == \
        {"tp": 1, "fp": 1, "fn": 1, "tn": 0}


def _claim(cid, start, end, verified=True):
    return Claim(id=cid, patient_id="p", note_id="n", category="physical_disability", condition="x",
                 qualifying_category=True, significantly_impairs=Tri.true, quote="q" * (end - start),
                 start=start, end=end, verified=verified)


def _label(start, end, **kw):
    base = dict(note_id="n", patient_id="p", category="physical_disability", qualifying=True, impairs="true",
                polarity="positive", text="t" * (end - start))
    return Label(start=start, end=end, **{**base, **kw})


def test_match_needs_half_the_shorter_span_and_is_one_to_one():
    labels = [_label(0, 40)]
    assert ev.match([_claim("a", 10, 40)], labels)["a"] is labels[0]          # clause inside the sentence
    assert ev.match([_claim("a", 35, 80)], labels)["a"] is None                # 5 chars of overlap
    m = ev.match([_claim("a", 0, 40), _claim("b", 0, 40)], labels)
    assert [m["a"], m["b"]] == [labels[0], None]


def test_claim_level_counts_verifier_drops_of_negatives_as_correct():
    labels = [_label(0, 40), _label(100, 140, qualifying=False, impairs="false", polarity="family")]
    claims = [_claim("keep", 0, 40), _claim("drop-bait", 100, 140, verified=False),
              _claim("drop-real", 0, 40, verified=False)]
    v = ev.claim_level(claims, labels)["verifier"]
    assert (v["kept"], v["dropped"], v["dropped_correctly"]) == (1, 2, 2)     # drop-real loses the 1:1 match


def test_simulated_outreach_clears_rosa_from_her_truth():
    rosa = next(c for c, _ in read_fixtures() if c.patient_id == "g-rosa")
    rosa = settle(rosa)[0]
    assert rosa.bucket == Bucket.ONE_AWAY
    after = ev.simulate_outreach(rosa, load_truth("g-rosa"))
    assert after.determination_final.status == "exempt"
    assert any(f.source.value == "patient_reply" and f.key == "standing_tolerance_minutes" and f.value == 10
               for f in after.facts)


def test_simulated_outreach_does_not_clear_someone_truly_unimpaired():
    bea = settle(next(c for c, _ in read_fixtures() if c.patient_id == "g-bea"))[0]
    assert ev.simulate_outreach(bea, load_truth("g-bea")).determination_final.status == "not_determined"


def test_committed_eval_json_matches_the_api_contract():
    data = json.loads((DATA_DIR / "eval.json").read_text())
    for who in ("a", "final"):
        assert set(data["patient_level"][who]) >= {"p", "r", "f1"}
    assert "confusion" in data["patient_level"]
    assert set(data["claim_level"]) >= {"p", "r", "f1"}
    assert set(data["verifier"]) >= {"kept", "dropped"}
    assert data["patient_level"]["after_outreach"]["simulated"] is True
    assert "synthetic" in data["label"]
    assert any(f["patient_id"] == "g-karen" for f in data["fragile"])
    assert data["patient_level"]["final"]["r"] > data["patient_level"]["a"]["r"]
