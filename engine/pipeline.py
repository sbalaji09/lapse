"""Runs the Lapse engines and writes results to the store. Idempotent; every stage is deterministic or cached.

    python -m engine.pipeline --stage cohort   # A1: cohort, truth, notes, labels, external databases
    python -m engine.pipeline                  # everything implemented so far

Later stages (A2 channel_a, A3 channel_b + verifier, A4 final/buckets/solver) register themselves in STAGES.
"""
import argparse
import collections
import time

from engine import external, notes as notegen, store
from engine.cohort import COHORT_SIZE, Patient, load_cohort
from engine.golden import BUILDERS
from engine.truth import Truth, generate as generate_truth, write_truth

GOLDEN_IDS = {f"g-{b.__name__}" for b in BUILDERS}


def stage_cohort() -> None:
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


STAGES = {"cohort": stage_cohort}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=sorted(STAGES), help="run a single stage (default: all, in order)")
    args = parser.parse_args()
    for name, run in STAGES.items():
        if args.stage in (None, name):
            run()


if __name__ == "__main__":
    main()
