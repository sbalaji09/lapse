"""Clinician-facing attestation loop: token lookup and sign/decline actions."""
import hashlib
import hmac
from datetime import datetime, timezone

import engine.config as config
import engine.store as store
from engine.models import Case, CaseStatus, Fact, Holder, Source

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

    clinician_missing = next(
        (m for m in case.missing if m.holder == Holder.clinician and m.status in ("open", "asked")),
        None,
    )

    if decision == "sign":
        if clinician_missing is not None:
            case.facts.append(Fact(
                id=f"fact-{case_id}-{clinician_missing.key}-{len(case.facts)}",
                patient_id=case_id,
                key=clinician_missing.key,
                value=True,
                source=Source.clinician_attestation,
                source_ref={"attestation_id": f"att-{case_id}"},
                quote=None,
                recorded_at=datetime.now(timezone.utc),
                rule_pack_version=config.ACTIVE_RULE_PACK,
            ))
            clinician_missing.status = "resolved_true"
        case.status = CaseStatus.attestation_ready
        case.events.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": "clinician_signed",
            "detail": {"clinician_name": case.clinician_name},
        })
    else:
        case.status = CaseStatus.needs_action
        case.events.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": "clinician_declined",
            "detail": {"clinician_name": case.clinician_name},
        })

    store.save_case(case)
    return {"status": case.status.value}
