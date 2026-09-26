"""Shared workflow status derivation after Track B changes case evidence."""

from engine.models import Case, CaseStatus, Holder


def status_after_reevaluation(
    case: Case,
    fallback: CaseStatus = CaseStatus.needs_action,
) -> CaseStatus:
    pending = next(
        (item for item in case.missing if item.status in ("open", "asked")),
        None,
    )
    if pending is None:
        return fallback
    if pending.holder == Holder.clinician:
        return CaseStatus.waiting_clinician
    if pending.holder == Holder.patient:
        return CaseStatus.waiting_patient
    return CaseStatus.needs_action
