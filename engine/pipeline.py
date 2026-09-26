"""Runs the Lapse engines and writes results to the store. Idempotent; every stage is deterministic or cached.

    python -m engine.pipeline --stage cohort      # A1: cohort, truth, notes, labels, external databases
    python -m engine.pipeline --stage channel_a   # A2: the state's ex parte check, zero LLM
    python -m engine.pipeline --stage channel_b   # A3: evidence finder + verifier (LLM, cached to disk)
          [--golden-only] [--estimate]
    python -m engine.pipeline --stage final       # A4: final determination, buckets, solver, database lookups
    python -m engine.pipeline                  # everything implemented so far

Later stages (A2 channel_a, A3 channel_b + verifier, A4 final/buckets/solver) register themselves in STAGES.
"""
import argparse
import collections
import json
import time

from engine import buckets, channel_b, external, llm, notes as notegen, store, verifier
from engine.channel_a import months_before
from engine.models import Bucket, Case, CaseStatus
from engine.cohort import COHORT_SIZE, Patient, load_cohort
from engine.config import AS_OF_DATE, MODEL_FAST, MODEL_VERIFY, PIPELINE_RUN_AT
from engine.golden import BUILDERS
from engine.channel_a import run_channel_a
from engine.rulepack import load_pack
from engine.truth import Truth, generate as generate_truth, load_truth, write_truth

GOLDEN_IDS = {f"g-{b.__name__}" for b in BUILDERS}


def stage_cohort(args=None) -> None:
    t0 = time.time()
    patients = load_cohort()
    if len(patients) != COHORT_SIZE:
        raise ValueError(f"cohort is {len(patients)}, expected {COHORT_SIZE}")
    missing = GOLDEN_IDS - {p.id for p in patients}
    if missing:
        raise ValueError(f"golden patients missing from cohort: {sorted(missing)}")

    truths = [generate_truth(p) for p in patients]
    fixture_notes = {case.patient_id: ns for case, ns in store.read_fixtures()}
    golden_notes = [n for ns in fixture_notes.values() for n in ns]
    all_notes, all_labels = list(golden_notes), notegen.golden_labels(golden_notes)
    for p, t in zip(patients, truths):
        if not p.golden:
            ns, lbs = notegen.generate(p, t)
            all_notes += ns
            all_labels += lbs

    notegen.check_labels(all_notes, all_labels)
    bad = [n.id for n in all_notes if not notegen.MIN_WORDS <= len(n.text.split()) <= notegen.MAX_WORDS]
    if bad:
        raise ValueError(f"{len(bad)} notes outside {notegen.MIN_WORDS}-{notegen.MAX_WORDS} words, e.g. {bad[:3]}")
    if len({n.id for n in all_notes}) != len(all_notes):
        raise ValueError("duplicate note ids")

    write_truth(truths)
    external.write(external.build(truths, {p.id: p.county for p in patients}))
    notegen.write_labels(all_labels)
    store.replace_cohort(patients, all_notes)
    print(f"cohort: {len(patients)} patients, {len(all_notes)} notes, {len(all_labels)} labels "
          f"(all offsets verified) in {time.time() - t0:.1f}s")
    print_truth_rates(patients, truths, all_labels)


def print_truth_rates(patients: list[Patient], truths: list[Truth], labels) -> None:
    n = len(truths)
    pct = lambda k: f"{k:4d}  {100 * k / n:5.1f}%"   # noqa: E731
    val = lambda t, k: t.facts[k].value              # noqa: E731
    rows = [
        ("income >= $580 in state wage records", sum(t.wage_monthly is not None and t.wage_monthly >= 580 for t in truths)),
        ("income >= $580 (all sources)", sum((val(t, "monthly_income") or 0) >= 580 for t in truths)),
        ("hours >= 80, known only to the patient", sum(val(t, "hours_per_month") >= 80 and
                                                       not (t.wage_monthly or 0) >= 580 for t in truths)),
        ("dependent child <= 13", sum(bool(val(t, "dependent_child_13_or_under")) for t in truths)),
        ("caregiver of disabled person", sum(bool(val(t, "caregiver_disabled_person")) for t in truths)),
        ("SNAP work-compliant (state-visible)", sum(bool(val(t, "snap_tanf_work_compliant")) for t in truths)),
        ("half-time student (not state-visible)", sum(bool(val(t, "enrolled_half_time_school")) for t in truths)),
        ("in SUD treatment", sum(bool(val(t, "in_sud_treatment")) for t in truths)),
        ("released from incarceration <= 90 days", sum(t.release_days_ago is not None and t.release_days_ago <= 90
                                                       for t in truths)),
        ("former foster youth (under 26)", sum(bool(val(t, "former_foster_youth")) for t in truths)),
        ("veteran, total disability", sum(bool(val(t, "veteran_total_disability")) for t in truths)),
        ("hardship county resident", sum(bool(val(t, "county_hardship")) for t in truths)),
        ("pregnant or postpartum", sum(bool(val(t, "pregnant_or_postpartum")) for t in truths)),
        ("American Indian / Alaska Native", sum(bool(val(t, "ai_an")) for t in truths)),
        ("qualifying condition", sum(bool(val(t, "qualifying_condition")) for t in truths)),
        ("  ...and significantly impaired (medically frail)", sum(bool(val(t, "significantly_impairs")) for t in truths)),
        ("  ...impairment written in notes", sum(any(g.impairs and g.documentation == "positive" for g in t.frail_groups)
                                                 for t in truths)),
        ("  ...impairment NOT in notes (ask the person)", sum(bool(val(t, "significantly_impairs")) and not any(
            g.impairs and g.documentation == "positive" for g in t.frail_groups) for t in truths)),
        ("TRULY exempt or compliant", sum(t.exempt_or_compliant for t in truths)),
    ]
    print("\ntruth rates (n = %d)" % n)
    for name, k in rows:
        print(f"  {name:<52}{pct(k)}")
    reasons = collections.Counter(r for t in truths for r in t.reasons)
    print("  satisfied rules:", ", ".join(f"{r} {c}" for r, c in reasons.most_common()))
    by_pol = collections.Counter(lb.polarity for lb in labels)
    print("  labels by polarity:", ", ".join(f"{k} {v}" for k, v in by_pol.most_common()))
    langs = collections.Counter(p.language for p in patients)
    print("  languages:", ", ".join(f"{k} {v}" for k, v in langs.most_common(5)))


def stage_channel_a(args=None) -> None:
    t0 = time.time()
    pack = load_pack()
    patients = store.list_patients()
    if not patients:
        raise RuntimeError("no patients in the store; run --stage cohort first")
    runs = [run_channel_a(p, pack) for p in patients]
    store.save_channel_runs("A", runs)

    n = len(runs)
    status = collections.Counter(d.status for d, _ in runs)
    rules = collections.Counter(r for d, _ in runs for r in d.rule_ids)
    print(f"channel A ({pack.version}): {n} patients in {time.time() - t0:.2f}s, zero LLM calls")
    for s in ("compliant", "exempt", "not_determined"):
        print(f"  a_{s:<16}{status[s]:5d}  {100 * status[s] / n:5.1f}%")
    print("  rules satisfied:", ", ".join(f"{r} {c}" for r, c in rules.most_common()))

    # Sanity against the answer key (the full eval is A5): how many the state's method drops.
    truth = {p.id: load_truth(p.id) for p in patients}
    safe = {d.patient_id for d, _ in runs if d.status != "not_determined"}
    really = {pid for pid, t in truth.items() if t.exempt_or_compliant}
    print(f"  truly exempt or compliant: {len(really)}; state clears {len(safe & really)} of them "
          f"({100 * len(safe & really) / len(really):.1f}% recall); "
          f"state clears {len(safe - really)} who truly are not")
    print(f"  -> {len(really - safe)} people who already qualify would get a notice")


# USD per 1M tokens (input, output), OpenAI list prices; used only for the printed estimate.
PRICES = {"gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00)}
CLAIMS_PER_NOTE_GUESS = 0.9


def _cost(model: str, tokens_in: float, tokens_out: float) -> float:
    pin, pout = PRICES.get(model, (0, 0))
    return (tokens_in * pin + tokens_out * pout) / 1e6


def stage_channel_b(args=None) -> None:
    golden_only = bool(args and args.golden_only)
    pack = load_pack()
    a_runs = store.list_channel_runs("A")
    if not a_runs:
        raise RuntimeError("no Channel A results; run --stage channel_a first")
    notes = [n for n in store.all_notes() if not golden_only or n.patient_id in GOLDEN_IDS]
    ext_calls = [channel_b.extraction_call(n) for n in notes]

    todo = llm.uncached(ext_calls)
    est_in = sum(len(c["system"]) + len(c["user"]) for c in todo) / 4
    n_verify = len(todo) * CLAIMS_PER_NOTE_GUESS
    ver_in = n_verify * (len(verifier.SYSTEM) + 250) / 4
    est = _cost(MODEL_FAST, est_in, len(todo) * 150) + _cost(MODEL_VERIFY, ver_in, n_verify * 40)
    minutes = (len(todo) + n_verify) / llm.CONCURRENCY * 2.5 / 60
    print(f"channel B: {len(notes)} notes, {len(notes) - len(todo)} cached, {len(todo)} to extract "
          f"(+~{n_verify:.0f} verifier calls). Estimate: ~${est:.2f}, ~{minutes:.1f} min.")
    if args and args.estimate:
        return

    t0 = time.time()
    responses = llm.run_batch(ext_calls)
    claims, unlocatable = [], 0
    for note, resp in zip(notes, responses):
        cs, bad = channel_b.to_claims(note, resp)
        claims += cs
        unlocatable += bad
    verdicts = llm.run_batch([verifier.verify_call(c) for c in claims])
    claims = [verifier.apply(c, v) for c, v in zip(claims, verdicts)]

    by_patient: dict[str, list] = collections.defaultdict(list)
    for c in claims:
        by_patient[c.patient_id].append(c)
    pids = {n.patient_id for n in notes}
    runs = []
    for pid in sorted(pids):
        b_facts = channel_b.evidence_facts(by_patient[pid], pack)
        a_facts = a_runs[pid][1]
        runs.append((channel_b.determine(pid, a_facts, b_facts, pack), b_facts))
    store.replace_claims(pids, claims)
    store.save_channel_runs("B", runs, replace_all=not golden_only)

    kept = sum(bool(c.verified) for c in claims)
    status = collections.Counter(d.status for d, _ in runs)
    cost = sum(_cost(m, llm.stats.input_tokens[m], llm.stats.output_tokens.get(m, 0)) for m in llm.stats.input_tokens)
    print(f"  {time.time() - t0:.1f}s; LLM calls: {llm.stats.misses} new (${cost:.2f}), {llm.stats.hits} from cache")
    print(f"  claims: {len(claims) + unlocatable} extracted, {kept} verified, {len(claims) - kept} dropped by the "
          f"verifier, {unlocatable} unlocatable (quote not found in the note)")
    print("  B status: " + ", ".join(f"{s} {status[s]}" for s in ("compliant", "exempt", "not_determined")))
    a_clear = sum(a_runs[d.patient_id][0].status != "not_determined" for d, _ in runs)
    b_clear = sum(d.status != "not_determined" for d, _ in runs)
    print(f"  cleared: state {a_clear} -> with notes {b_clear} (+{b_clear - a_clear})")


# The live demo resolves this one by hand ("Check student enrollment"), so the batch run leaves it open.
DEMO_DATABASE_CLICK = ("g-deshawn", "enrolled_half_time_school")


def _summary_event(case, db_hits: list[str]) -> str:
    from engine.solver import DB_LABELS, RULE_NAMES

    a = case.determination_a
    names = ", ".join(RULE_NAMES[r] for r in case.determination_final.rule_ids)
    if a.status == "compliant":
        return f"Meets the requirement through {names} in state records. Never contacted."
    if a.status == "exempt":
        text = f"State exempts through {names}."
        return text + (" No verified sentence in the chart supports an impairment. Flagged fragile."
                       if case.fragile else " No action needed.")
    if case.bucket == Bucket.SAFE:
        return f"Resolved from {' and '.join(DB_LABELS[d] for d in db_hits)}. Nobody contacted."
    if case.bucket == Bucket.PROVABLE:
        return f"State check could not determine. The chart already supports {names}; ready for clinician signature."
    if case.bucket == Bucket.ONE_AWAY:
        return f"State check could not determine. One fact away: {case.missing[0].why}"
    return "No exemption within one fact. Needs help reporting hours."


def stage_final(args=None) -> None:
    from engine.solver import default_status, settle

    t0 = time.time()
    pack = load_pack()
    patients = store.list_patients()
    a_runs, b_runs = store.list_channel_runs("A"), store.list_channel_runs("B")
    missing_b = [p.id for p in patients if p.id not in b_runs]
    if missing_b:
        raise RuntimeError(f"no Channel B result for {len(missing_b)} patients; run --stage channel_b first")
    claims: dict[str, list] = collections.defaultdict(list)
    for c in store.list_claims():
        claims[c.patient_id].append(c)

    cases, db_cleared = [], 0
    for p in patients:
        det_a, a_facts = a_runs[p.id]
        b_facts = b_runs[p.id][1]
        verified = [c for c in claims[p.id] if c.verified]
        case = Case(
            patient_id=p.id, display_name=p.display_name, age=p.age, language=p.language, email=p.email,
            phone=p.phone, clinic_id=p.clinic_id, clinician_name=p.clinician_name, renewal_date=p.renewal_date,
            bucket=Bucket.NO_PATH, fragile=buckets.is_fragile(det_a, claims[p.id]), status=CaseStatus.no_action,
            determination_a=det_a, determination_final=det_a.model_copy(update={"channel": "final"}),
            claims=verified, dropped_claims=[c for c in claims[p.id] if c.verified is False],
            facts=a_facts + b_facts, missing=[], billed_dx_12mo=p.billed_dx(months_before(AS_OF_DATE, pack.lookback_months)),
            events=[],
        )
        keep_open = (DEMO_DATABASE_CLICK[1],) if p.id == DEMO_DATABASE_CLICK[0] else ()
        case, db_hits = settle(case, pack, keep_open=keep_open, at=PIPELINE_RUN_AT)
        if db_hits and case.bucket == Bucket.SAFE:
            db_cleared += 1
        case.status = default_status(case)
        case.events = [{"at": PIPELINE_RUN_AT.isoformat(), "kind": "pipeline_run", "detail": _summary_event(case, db_hits)}]
        cases.append(case)
    store.replace_cases(cases)
    store.save_baseline([c for c in cases if c.patient_id in GOLDEN_IDS])     # what `make reset` restores

    n = len(cases)
    cleared = lambda d: d.status != "not_determined"   # noqa: E731
    summary = {
        "cohort": n,
        "a_exempt": sum(cleared(c.determination_a) for c in cases),
        "a_not_determined": sum(not cleared(c.determination_a) for c in cases),
        "final_exempt": sum(cleared(c.determination_final) for c in cases),
        "provable": sum(c.bucket == Bucket.PROVABLE for c in cases),
        "one_away": sum(c.bucket == Bucket.ONE_AWAY for c in cases),
        "no_path": sum(c.bucket == Bucket.NO_PATH for c in cases),
        "fragile": sum(c.fragile for c in cases),
        "recovered": sum(cleared(c.determination_final) and not cleared(c.determination_a) for c in cases),
        "database_resolved_no_contact": db_cleared,
        "verifier_dropped": sum(len(c.dropped_claims) for c in cases),
        "rule_pack": pack.version,
    }
    print(f"final: {n} cases in {time.time() - t0:.1f}s")
    print(json.dumps(summary, indent=1))
    top = collections.Counter((c.missing[0].key, c.missing[0].holder.value) for c in cases if c.bucket == Bucket.ONE_AWAY)
    print("  ONE_AWAY top missing fact:", ", ".join(f"{k}/{h} {v}" for (k, h), v in top.most_common()))


STAGES = {"cohort": stage_cohort, "channel_a": stage_channel_a, "channel_b": stage_channel_b, "final": stage_final}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=sorted(STAGES), help="run a single stage (default: all, in order)")
    parser.add_argument("--golden-only", action="store_true", help="channel_b: only the 7 golden patients")
    parser.add_argument("--estimate", action="store_true", help="channel_b: print the cost estimate and stop")
    args = parser.parse_args()
    for name, run in STAGES.items():
        if args.stage in (None, name):
            run(args)


if __name__ == "__main__":
    main()
