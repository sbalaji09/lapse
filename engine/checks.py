"""Integrity checks every write path must pass: spans point at real text, facts use registry keys."""
from functools import cache

import yaml

from engine.config import FACT_REGISTRY_PATH
from engine.models import Case, Note, Source


class IntegrityError(ValueError):
    pass


@cache
def registry_keys() -> frozenset[str]:
    return frozenset(yaml.safe_load(FACT_REGISTRY_PATH.read_text()))


def check_case(case: Case, notes: list[Note]) -> None:
    """Raise IntegrityError on the first broken span, unknown fact key, or dangling reference."""
    by_id = {n.id: n for n in notes}
    for n in notes:
        if n.patient_id != case.patient_id:
            raise IntegrityError(f"{case.patient_id}: note {n.id} belongs to {n.patient_id}")

    for c in case.claims + case.dropped_claims:
        note = by_id.get(c.note_id)
        if note is None:
            raise IntegrityError(f"{case.patient_id}: claim {c.id} points at missing note {c.note_id}")
        if note.text[c.start:c.end] != c.quote:
            raise IntegrityError(f"{case.patient_id}: claim {c.id} quote does not match note text at {c.start}:{c.end}")

    keys = registry_keys()
    fact_ids = {f.id for f in case.facts}
    for f in case.facts:
        if f.key not in keys:
            raise IntegrityError(f"{case.patient_id}: fact {f.id} uses unregistered key {f.key!r}")
        if f.source == Source.note_span:
            ref = f.source_ref
            note = by_id.get(ref.get("note_id"))
            if note is None or note.text[ref["start"]:ref["end"]] != f.quote:
                raise IntegrityError(f"{case.patient_id}: fact {f.id} span does not match its note")

    for m in case.missing:
        if m.key not in keys:
            raise IntegrityError(f"{case.patient_id}: missing fact {m.id} uses unregistered key {m.key!r}")

    for d in (case.determination_a, case.determination_final):
        dangling = set(d.fact_ids) - fact_ids
        if dangling:
            raise IntegrityError(f"{case.patient_id}: determination {d.channel} cites unknown facts {sorted(dangling)}")
