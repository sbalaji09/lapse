"""Clinician-facing attestation loop: token lookup and sign/decline actions."""
import hashlib
import hmac
from datetime import datetime, timezone

import engine.solver as solver
import engine.store as store
from engine.models import Case, CaseStatus, Fact, Holder, Source
from engine.loop.workflow import status_after_reevaluation

CLINICIAN_TOKEN_SECRET = b"lapse-demo-secret"  # hackathon demo only, not real auth


def clinician_token(case_id: str) -> str:
    return hmac.new(CLINICIAN_TOKEN_SECRET, case_id.encode(), hashlib.sha256).hexdigest()[:16]


def case_for_token(token: str) -> Case | None:
    for case in store.list_cases():
        if clinician_token(case.patient_id) == token:
            return case
    return None


def sign_clinician(case_id: str, decision: str) -> dict:
    if decision not in ("sign", "decline"):
        raise ValueError(f"invalid decision: {decision}")

    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"no such case: {case_id}")

    pending = next(
        (
            m for m in case.missing
            if m.status in ("open", "asked")
        ),
        None,
    )
    if pending is None or pending.holder != Holder.clinician:
        raise ValueError("no pending clinician attestation")

    if decision == "sign":
        case.facts.append(Fact(
            id=f"fact-{case_id}-{pending.key}-{len(case.facts)}",
            patient_id=case_id,
            key=pending.key,
            value=True,
            source=Source.clinician_attestation,
            source_ref={"attestation_id": f"att-{case_id}"},
            quote=None,
            recorded_at=datetime.now(timezone.utc),
            rule_pack_version=case.determination_final.rule_pack_version,
        ))
        pending.status = "resolved_true"
        case = solver.reevaluate(case)
        case.status = status_after_reevaluation(
            case,
            fallback=CaseStatus.attestation_ready,
        )
        case.events.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": "clinician_signed",
            "detail": {"clinician_name": case.clinician_name},
        })
    else:
        pending.status = "resolved_false"
        case.status = CaseStatus.needs_action
        case.events.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": "clinician_declined",
            "detail": {"clinician_name": case.clinician_name},
        })

    store.save_case(case)
    return {"status": case.status.value}


MAX_SENTENCES = 3


def clinician_card(case: Case) -> dict:
    """The one screen a clinician sees: one question and up to three sentences of evidence, each pointing at
    its exact source. The patient's own words come first when there are any."""
    from engine.models import Tri
    from engine.solver import condition_name

    condition = condition_name(case)
    ask = next((m for m in case.missing if m.holder == Holder.clinician and m.status in ("open", "asked")), None)
    question = ask.question["en"] if ask else f"Does the record support that {condition} limits {case.display_name.split()[0]}'s ability to work?"

    spans = []
    for f in case.facts:
        if f.source == Source.patient_reply and f.quote and f.key in ("standing_tolerance_minutes", "significantly_impairs"):
            received = f.recorded_at.date().isoformat()
            spans.append({"source": "patient_reply", "quote": f.quote, "date": received,
                          "label": f"Patient's own words, received {received}",
                          "message_id": f.source_ref.get("message_id")})
    notes = {n.id: n for n in store.get_notes(case.patient_id)}
    ranked = sorted((c for c in case.claims if c.verified and c.qualifying_category),
                    key=lambda c: (c.significantly_impairs != Tri.true, notes[c.note_id].date if c.note_id in notes else None))
    for c in ranked:
        if len(spans) >= MAX_SENTENCES:
            break
        note = notes.get(c.note_id)
        spans.append({"source": "note_span", "note_id": c.note_id, "quote": c.quote, "start": c.start, "end": c.end,
                      "date": note.date.isoformat() if note else None, "author": note.author if note else None})
    return {"patient_id": case.patient_id, "name": case.display_name, "status": case.status.value,
            "clinician_name": case.clinician_name, "question": question, "condition": condition,
            "spans": spans[:MAX_SENTENCES],
            "attesting_to": "You're attesting to what the record shows. The state makes the eligibility decision."}
