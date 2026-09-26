"""Accuracy against labels we wrote on synthetic data. Nothing here feeds back into the pipeline.

    python -m engine.eval      # writes data/eval.json (served by /api/eval) and prints the pitch numbers

Patient level - the label is the truth file's exempt_or_compliant, which uses ALL truth facts:
  a        the state's method (Channel A)
  final    Lapse before contacting anyone (state data + verified notes + database lookups)
  after_outreach (SIMULATED): every ONE_AWAY person answers their question from the truth, the clinician
           attests from the truth, and the case is re-evaluated.
Claim level - against data/truth/labels.jsonl; a claim matches a label in the same note when the spans overlap
by at least half of the shorter one:
  impairment   verified claims saying significantly_impairs=true vs labels stating a limitation
  condition    verified qualifying claims vs labels establishing a current qualifying condition
  verifier     kept vs dropped, and whether each drop removed a real negative
"""
import collections
import json
from datetime import datetime

from engine import store
from engine.config import DATA_DIR, LABELS_PATH, PIPELINE_RUN_AT
from engine.models import Bucket, Case, Fact, Holder, Source, Tri
from engine.notes import Label
from engine.rulepack import load_pack
from engine.solver import reevaluate
from engine.truth import load_truth

EVAL_PATH = DATA_DIR / "eval.json"
LABEL_NOTE = "Measured against labels we wrote on synthetic data."
POSITIVE_CONDITION = {"positive", "unstated", "remission"}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"p": round(p, 3), "r": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


def confusion(pred: dict[str, bool], truth: dict[str, bool]) -> dict:
    c = collections.Counter((pred[k], truth[k]) for k in truth)
    return {"tp": c[(True, True)], "fp": c[(True, False)], "fn": c[(False, True)], "tn": c[(False, False)]}


def _cleared(det) -> bool:
    return det.status != "not_determined"


# ---------------------------------------------------------------------------------------------
# Simulated outreach: answer each ONE_AWAY question from the truth
# ---------------------------------------------------------------------------------------------

SOURCE_FOR = {Holder.patient: Source.patient_reply, Holder.clinician: Source.clinician_attestation,
              Holder.database: Source.external_db}


def simulate_outreach(case: Case, truth, max_rounds: int = 4) -> Case:
    """Answer open missing facts from the truth until the case clears or nothing answerable is left."""
    for _ in range(max_rounds):
        if _cleared(case.determination_final):
            return case
        open_items = [m for m in case.missing if m.status == "open"]
        if not open_items:
            return case
        m = open_items[0]
        tf = truth.facts.get(m.key)
        if tf is None or tf.value is None:
            m.status = "answered"       # the person could not say; nothing to record
        else:
            case.facts.append(Fact(id=f"fs-{case.patient_id}-{m.key}", patient_id=case.patient_id, key=m.key,
                                   value=tf.value, source=SOURCE_FOR[m.holder], source_ref={"simulated": True},
                                   recorded_at=PIPELINE_RUN_AT, rule_pack_version=case.determination_final.rule_pack_version))
            m.status = "resolved_true" if tf.value is True else "answered"
        case = reevaluate(case)
    return case


# ---------------------------------------------------------------------------------------------
# Claim level
# ---------------------------------------------------------------------------------------------

def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    return inter > 0 and inter >= 0.5 * min(a[1] - a[0], b[1] - b[0])


def match(claims, labels) -> dict[str, Label | None]:
    """claim id -> the label it matches (same note, >= 50% overlap), one-to-one, greedy by overlap order."""
    by_note = collections.defaultdict(list)
    for lb in labels:
        by_note[lb.note_id].append(lb)
    used, out = set(), {}
    for c in sorted(claims, key=lambda c: (c.note_id, c.start)):
        hit = next((lb for lb in by_note[c.note_id]
                    if id(lb) not in used and _overlaps((c.start, c.end), (lb.start, lb.end))), None)
        if hit is not None:
            used.add(id(hit))
        out[c.id] = hit
    return out


def _task(claims, labels, claim_ok, label_ok) -> dict:
    predicted = [c for c in claims if c.verified and claim_ok(c)]
    wanted = [lb for lb in labels if label_ok(lb)]
    matched = match(predicted, wanted)
    tp = sum(v is not None for v in matched.values())
    return {**prf(tp, len(predicted) - tp, len(wanted) - tp), "predicted": len(predicted), "labels": len(wanted)}


def claim_level(claims, labels) -> dict:
    impairment = _task(claims, labels,
                       lambda c: c.qualifying_category and c.significantly_impairs == Tri.true,
                       lambda lb: lb.qualifying and lb.impairs == "true")
    condition = _task(claims, labels,
                      lambda c: c.qualifying_category and c.category != "sud_treatment",
                      lambda lb: lb.qualifying and lb.polarity in POSITIVE_CONDITION)

    matched = match(claims, labels)
    is_evidence = lambda lb: lb is not None and (lb.qualifying and lb.polarity in POSITIVE_CONDITION  # noqa: E731
                                                  or lb.fact_key == "in_sud_treatment")
    kept = [c for c in claims if c.verified]
    dropped = [c for c in claims if c.verified is False]
    verifier = {
        "kept": len(kept),
        "dropped": len(dropped),
        "dropped_correctly": sum(not is_evidence(matched[c.id]) for c in dropped),
        "dropped_real_evidence": sum(is_evidence(matched[c.id]) for c in dropped),
        "extraction_precision_before_verifier": prf(sum(is_evidence(matched[c.id]) for c in claims),
                                                    sum(not is_evidence(matched[c.id]) for c in claims), 0)["p"],
        "precision_after_verifier": prf(sum(is_evidence(matched[c.id]) for c in kept),
                                        sum(not is_evidence(matched[c.id]) for c in kept), 0)["p"],
    }
    return {**{k: impairment[k] for k in ("p", "r", "f1")}, "impairment": impairment, "condition": condition,
            "verifier": verifier}


# ---------------------------------------------------------------------------------------------
# Fragile list
# ---------------------------------------------------------------------------------------------

def fragile_list(cases: list[Case], truths: dict) -> list[dict]:
    notes_per = collections.Counter(n.patient_id for n in store.all_notes())
    out = []
    for c in sorted((c for c in cases if c.fragile), key=lambda c: c.renewal_date):
        code = next((f.source_ref for f in c.facts if f.key == "qualifying_condition" and f.source == Source.billing_code), {})
        n = notes_per[c.patient_id]
        out.append({"patient_id": c.patient_id, "name": c.display_name, "renewal_date": c.renewal_date.isoformat(),
                    "state_code": code.get("code"), "state_code_display": code.get("display"),
                    "claim_id": code.get("claim_id"),
                    "finding": f"No supporting sentence found in {n} note{'s' if n != 1 else ''}.",
                    "truly_exempt_or_compliant": truths[c.patient_id].exempt_or_compliant,
                    "truly_medically_frail": "medically_frail" in truths[c.patient_id].reasons})
    return out


# ---------------------------------------------------------------------------------------------

def run() -> dict:
    load_pack()
    cases = store.list_cases()
    if len(cases) < 100:
        raise RuntimeError("store holds fixtures only; run `python -m engine.pipeline` first")
    truths = {c.patient_id: load_truth(c.patient_id) for c in cases}
    label = {pid: t.exempt_or_compliant for pid, t in truths.items()}

    pred_a = {c.patient_id: _cleared(c.determination_a) for c in cases}
    pred_final = {c.patient_id: _cleared(c.determination_final) for c in cases}
    after = {c.patient_id: _cleared(simulate_outreach(c.model_copy(deep=True), truths[c.patient_id]).determination_final)
             if c.bucket == Bucket.ONE_AWAY else pred_final[c.patient_id] for c in cases}
    conf = {"a": confusion(pred_a, label), "final": confusion(pred_final, label),
            "after_outreach": confusion(after, label)}

    rows = [json.loads(line) for line in LABELS_PATH.read_text().splitlines()]
    labels = [Label(**r) for r in rows]
    claims = store.list_claims()
    cl = claim_level(claims, labels)

    one_away = [c for c in cases if c.bucket == Bucket.ONE_AWAY]
    result = {
        "label": LABEL_NOTE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "patient_level": {
            "a": prf(conf["a"]["tp"], conf["a"]["fp"], conf["a"]["fn"]),
            "final": prf(conf["final"]["tp"], conf["final"]["fp"], conf["final"]["fn"]),
            "after_outreach": {**prf(conf["after_outreach"]["tp"], conf["after_outreach"]["fp"],
                                     conf["after_outreach"]["fn"]),
                               "simulated": True,
                               "note": "Every ONE_AWAY question answered from the synthetic truth, then re-evaluated."},
            "confusion": conf,
            "truly_qualify": sum(label.values()),
        },
        "claim_level": {k: cl[k] for k in ("p", "r", "f1", "impairment", "condition")},
        "verifier": cl["verifier"],
        "one_away": {"count": len(one_away),
                     "truly_qualify": sum(label[c.patient_id] for c in one_away),
                     "cleared_after_simulated_outreach": sum(after[c.patient_id] for c in one_away)},
        "fragile": fragile_list(cases, truths),
        "pitch": pitch_numbers(cases, label),
    }
    EVAL_PATH.write_text(json.dumps(result, indent=1) + "\n")
    return result


def pitch_numbers(cases: list[Case], label: dict[str, bool]) -> dict:
    a = sum(_cleared(c.determination_a) for c in cases)
    final = sum(_cleared(c.determination_final) for c in cases)
    return {
        "cohort": len(cases),
        "state_clears": a,
        "state_cannot_determine": len(cases) - a,
        "lapse_clears_before_outreach": final,
        "already_qualify_but_dropped_by_state": sum(label[c.patient_id] and not _cleared(c.determination_a)
                                                    for c in cases),
        "recovered_from_chart_and_databases": final - a,
        "one_away": sum(c.bucket == Bucket.ONE_AWAY for c in cases),
        "provable": sum(c.bucket == Bucket.PROVABLE for c in cases),
        "fragile": sum(c.fragile for c in cases),
        "database_resolved_no_contact": sum(
            c.bucket == Bucket.SAFE and not _cleared(c.determination_a) for c in cases),
    }


def main() -> None:
    r = run()
    pl, cl, v = r["patient_level"], r["claim_level"], r["verifier"]
    print(r["label"])
    print(f"\npatient level (n = {sum(pl['confusion']['a'].values())}, truly qualify = {pl['truly_qualify']})")
    print(f"  {'':<26}{'precision':>10}{'recall':>9}{'F1':>7}")
    for name, key in (("state method (A)", "a"), ("Lapse, before outreach", "final"),
                      ("Lapse, after outreach*", "after_outreach")):
        m = pl[key]
        print(f"  {name:<26}{m['p']:>10.3f}{m['r']:>9.3f}{m['f1']:>7.3f}   {pl['confusion'][key]}")
    print("  * simulated: every ONE_AWAY question answered from the synthetic truth")
    print("\nclaim level")
    for name in ("impairment", "condition"):
        m = cl[name]
        print(f"  {name:<12} p {m['p']:.3f}  r {m['r']:.3f}  f1 {m['f1']:.3f}  ({m['predicted']} predicted, {m['labels']} labels)")
    print(f"  verifier: kept {v['kept']}, dropped {v['dropped']} ({v['dropped_correctly']} correctly, "
          f"{v['dropped_real_evidence']} real evidence); precision {v['extraction_precision_before_verifier']:.3f} "
          f"-> {v['precision_after_verifier']:.3f}")
    print(f"\nfragile: {len(r['fragile'])} (state exempts on a frailty code, no supporting sentence); "
          f"{sum(not f['truly_medically_frail'] for f in r['fragile'])} are not truly medically frail")
    for f in r["fragile"][:5]:
        print(f"  {f['name']:<24}{f['state_code_display'] or '':<46}{f['finding']}")
    print("\npitch numbers")
    for k, v in r["pitch"].items():
        print(f"  {k:<40}{v}")
    print(f"\nwrote {EVAL_PATH}")


if __name__ == "__main__":
    main()
