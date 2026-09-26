"""Outbound patient-ask email flow. Mock/log only: no Resend network call.

# ponytail: no real email send here, log-only preview; wire Resend when we have a key + domain.
"""
from datetime import datetime, timezone

from engine.models import CaseStatus, Holder, MissingFact
import engine.store as store

TEMPLATES = {
    "en": {
        "subject": "Quick question about your coverage review",
        "body": (
            "Hi {name},\n\n"
            "As part of your Medi-Cal coverage review (due {renewal_date}), we have one quick question:\n\n"
            "{question}\n\n"
            "Please reply in your own words, in any language you're comfortable with. No login needed.\n\n"
            "Thank you,\n{clinic_name}"
        ),
    },
    "es": {
        "subject": "Una pregunta rápida sobre su revisión de cobertura",
        "body": (
            "Hola {name},\n\n"
            "Como parte de su revisión de cobertura de Medi-Cal (vence el {renewal_date}), tenemos una pregunta rápida:\n\n"
            "{question}\n\n"
            "Por favor responda con sus propias palabras, en cualquier idioma que prefiera. No necesita iniciar sesión.\n\n"
            "Gracias,\n{clinic_name}"
        ),
    },
}


def top_patient_missing(case) -> MissingFact | None:
    for m in case.missing:
        if m.holder == Holder.patient and m.status == "open":
            return m
    return None


def render_email(case, missing_fact: MissingFact) -> dict:
    lang = case.language if case.language in TEMPLATES else "en"
    tpl = TEMPLATES[lang]
    question = missing_fact.question.get(lang) or missing_fact.question.get("en", "")
    fields = dict(
        name=case.display_name,
        renewal_date=case.renewal_date.isoformat(),
        question=question,
        clinic_name=case.clinician_name or "your clinic",
    )
    return {"subject": tpl["subject"], "body": tpl["body"].format(**fields)}


def send_ask(case_id: str) -> dict:
    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"no such case: {case_id}")

    missing_fact = top_patient_missing(case)
    if missing_fact is None:
        raise ValueError(f"no open patient-held missing facts for case: {case_id}")

    email = render_email(case, missing_fact)
    message_id = f"msg-{case_id}-{missing_fact.id}"

    missing_fact.status = "asked"
    case.status = CaseStatus.waiting_patient
    case.events.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": "patient_asked",
        "detail": {"message_id": message_id, "subject": email["subject"], "body": email["body"]},
    })
    store.save_case(case)

    return {"channel": "email", "message_id": message_id, "preview": email}
