"""Outbound patient-ask email flow with local preview and Resend delivery."""

from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Callable, Protocol
from urllib import error, request

import certifi

from engine import guardrails
from engine.config import CLINICS, DEMO_ROSA_EMAIL
from engine.models import CaseStatus, Holder, MissingFact
import engine.store as store

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_RESEND_FROM = "Lapse <onboarding@resend.dev>"

TEMPLATES = {
    "en": {
        "subject": "Quick question about your coverage review",
        "body": (
            "Hi {name},\n\n"
            "As part of your Medi-Cal coverage review (due {renewal_date}), we have one quick question:\n\n"
            "{question}\n\n"
            "Please reply in your own words, in any language you're comfortable with. No login needed.\n\n"
            "Thank you,\n{clinic_name}\n{clinic_phone}"
        ),
    },
    "es": {
        "subject": "Una pregunta rápida sobre su revisión de cobertura",
        "body": (
            "Hola {name},\n\n"
            "Como parte de su revisión de cobertura de Medi-Cal (vence el {renewal_date}), tenemos una pregunta rápida:\n\n"
            "{question}\n\n"
            "Por favor responda con sus propias palabras, en cualquier idioma que prefiera. No necesita iniciar sesión.\n\n"
            "Gracias,\n{clinic_name}\n{clinic_phone}"
        ),
    },
}

GENERAL_TEMPLATES = {
    "en": {
        "subject": "A reminder from your care team",
        "body": (
            "Hi {name},\n\n"
            "Your care team is reaching out about your Medi-Cal coverage review, due {renewal_date}. "
            "Please contact {clinic_name} at {clinic_phone} if you have questions or need help with the review.\n\n"
            "Thank you,\n{clinic_name}"
        ),
    },
    "es": {
        "subject": "Un recordatorio de su equipo de atención",
        "body": (
            "Hola {name},\n\n"
            "Su equipo de atención se comunica con usted sobre su revisión de cobertura de Medi-Cal, "
            "que vence el {renewal_date}. Comuníquese con {clinic_name} al {clinic_phone} si tiene "
            "preguntas o necesita ayuda con la revisión.\n\n"
            "Gracias,\n{clinic_name}"
        ),
    },
}


def _secure_urlopen(outgoing_request, timeout: int):
    context = ssl.create_default_context(cafile=certifi.where())
    return request.urlopen(outgoing_request, timeout=timeout, context=context)


@dataclass(frozen=True)
class EmailSettings:
    backend: str
    resend_api_key: str | None
    from_email: str
    timeout_seconds: int


def email_settings() -> EmailSettings:
    api_key = os.environ.get("RESEND_API_KEY") or None
    backend = os.environ.get("EMAIL_BACKEND")
    if not backend:
        backend = "resend" if api_key else "local"
    try:
        timeout = max(1, int(os.environ.get("EMAIL_TIMEOUT_SECONDS", "15")))
    except ValueError:
        timeout = 15
    return EmailSettings(
        backend=backend.strip().lower(),
        resend_api_key=api_key,
        from_email=os.environ.get("RESEND_FROM_EMAIL", DEFAULT_RESEND_FROM),
        timeout_seconds=timeout,
    )


def email_configuration_errors(settings: EmailSettings | None = None) -> list[str]:
    settings = settings or email_settings()
    errors: list[str] = []
    if settings.backend not in {"local", "resend"}:
        errors.append("EMAIL_BACKEND must be local or resend")
    if settings.backend == "resend" and not settings.resend_api_key:
        errors.append("RESEND_API_KEY is required for EMAIL_BACKEND=resend")
    if settings.backend == "resend" and not settings.from_email.strip():
        errors.append("RESEND_FROM_EMAIL is required for EMAIL_BACKEND=resend")
    return errors


def email_health() -> dict:
    settings = email_settings()
    errors = email_configuration_errors(settings)
    return {
        "configured": not errors,
        "backend": settings.backend,
        "from_configured": bool(settings.from_email.strip()),
        "errors": errors,
    }


def top_patient_missing(case, *, include_asked: bool = False) -> MissingFact | None:
    statuses = {"open", "asked"} if include_asked else {"open"}
    for missing in case.missing:
        if missing.holder == Holder.patient and missing.status in statuses:
            return missing
    return None


def render_email(case, missing_fact: MissingFact) -> dict:
    lang = case.language if case.language in TEMPLATES else "en"
    template = TEMPLATES[lang]
    question = missing_fact.question.get(lang) or missing_fact.question.get("en", "")
    clinic = CLINICS.get(case.clinic_id, {})
    fields = {
        "name": case.display_name.split()[0],
        "renewal_date": case.renewal_date.isoformat(),
        "question": question,
        "clinic_name": clinic.get("name", "your clinic"),
        "clinic_phone": clinic.get("phone", ""),
    }
    return {
        "subject": template["subject"],
        "body": template["body"].format(**fields),
    }


def render_general_email(case) -> dict:
    lang = case.language if case.language in GENERAL_TEMPLATES else "en"
    template = GENERAL_TEMPLATES[lang]
    clinic = CLINICS.get(case.clinic_id, {})
    fields = {
        "name": case.display_name.split()[0],
        "renewal_date": case.renewal_date.isoformat(),
        "clinic_name": clinic.get("name", "your clinic"),
        "clinic_phone": clinic.get("phone", ""),
    }
    return {
        "subject": template["subject"],
        "body": template["body"].format(**fields),
    }


def email_recipient(case) -> str:
    if case.patient_id == "g-rosa":
        return os.environ.get("DEMO_INBOX") or DEMO_ROSA_EMAIL
    return case.email


def is_dummy_recipient(recipient: str) -> bool:
    address = parseaddr(recipient)[1].lower()
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    return domain == "example.com" or domain.endswith(".example.com")


class EmailProvider(Protocol):
    name: str

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> dict:
        ...


class LocalEmailProvider:
    name = "local"

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> dict:
        return {"provider_message_id": idempotency_key, "delivery": "preview"}


class ResendEmailProvider:
    name = "resend"

    def __init__(
        self,
        settings: EmailSettings | None = None,
        opener: Callable | None = None,
    ):
        self.settings = settings or email_settings()
        errors = email_configuration_errors(self.settings)
        if errors:
            raise RuntimeError("; ".join(errors))
        self.opener = opener or _secure_urlopen

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> dict:
        payload = json.dumps(
            {
                "from": self.settings.from_email,
                "to": [recipient],
                "subject": subject,
                "text": body,
            }
        ).encode()
        outgoing = request.Request(
            RESEND_API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.settings.resend_api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
                "User-Agent": "lapse-email/1.0",
            },
            method="POST",
        )
        try:
            response = self.opener(outgoing, timeout=self.settings.timeout_seconds)
            result = json.loads(response.read().decode())
        except error.HTTPError as exc:
            raise RuntimeError(f"Resend email request failed with HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Resend email request failed") from exc
        if not result.get("id"):
            raise RuntimeError("Resend response did not include an email ID")
        return {"provider_message_id": result["id"], "delivery": "sent"}


def _provider(settings: EmailSettings) -> EmailProvider:
    if settings.backend == "local":
        return LocalEmailProvider()
    if settings.backend == "resend":
        return ResendEmailProvider(settings)
    raise RuntimeError(f"unsupported email backend: {settings.backend}")


def _send_email(
    case_id: str,
    *,
    allow_resend: bool,
    allow_general: bool,
    provider: EmailProvider | None = None,
    settings: EmailSettings | None = None,
) -> dict:
    settings = settings or email_settings()
    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"no such case: {case_id}")

    missing_fact = top_patient_missing(case, include_asked=allow_resend)
    if missing_fact is None and not allow_general:
        raise ValueError(f"no open patient-held missing facts for case: {case_id}")
    recipient = email_recipient(case)
    if not recipient:
        raise ValueError(f"no email address for case: {case_id}")

    email = render_email(case, missing_fact) if missing_fact else render_general_email(case)
    guardrail_assessment = guardrails.enforce_patient_message(
        f"{email['subject']}\n\n{email['body']}"
    )

    def is_same_outreach(event: dict) -> bool:
        detail = event.get("detail") or {}
        if missing_fact:
            return (
                event.get("kind") == "patient_asked"
                and detail.get("missing_fact_id") in {None, missing_fact.id}
            )
        return (
            event.get("kind") == "patient_email_sent"
            and detail.get("purpose") == "general_reminder"
        )

    prior_attempts = sum(is_same_outreach(event) for event in case.events)
    attempt = prior_attempts + 1
    if missing_fact:
        base_message_id = f"msg-{case_id}-{missing_fact.id}"
    else:
        base_message_id = f"msg-{case_id}-general"
    message_id = base_message_id if attempt == 1 else f"{base_message_id}-{attempt}"
    selected_provider = provider
    if selected_provider is None:
        selected_provider = (
            LocalEmailProvider() if is_dummy_recipient(recipient) else _provider(settings)
        )
    delivery = selected_provider.send(
        recipient=recipient,
        subject=email["subject"],
        body=email["body"],
        idempotency_key=message_id,
    )

    sent_at = datetime.now(timezone.utc)
    if missing_fact:
        missing_fact.status = "asked"
        case.status = CaseStatus.waiting_patient
    case.events.append(
        {
            "at": sent_at.isoformat(),
            "kind": "patient_asked" if missing_fact else "patient_email_sent",
            "detail": {
                "message_id": message_id,
                "missing_fact_id": missing_fact.id if missing_fact else None,
                "purpose": "question" if missing_fact else "general_reminder",
                "attempt": attempt,
                "provider_message_id": delivery["provider_message_id"],
                "provider": selected_provider.name,
                "delivery": delivery["delivery"],
                "to": recipient,
                "subject": email["subject"],
                "body": email["body"],
                "guardrail": {
                    "provider": guardrail_assessment["provider"],
                    "action": guardrail_assessment["action"],
                    "topics": guardrail_assessment["topics"],
                },
            },
        }
    )
    store.save_case(case)

    return {
        "channel": "email",
        "message_id": message_id,
        "provider_message_id": delivery["provider_message_id"],
        "provider": selected_provider.name,
        "delivery": delivery["delivery"],
        "to": recipient,
        "purpose": "question" if missing_fact else "general_reminder",
        "attempt": attempt,
        "preview": email,
        "guardrail": {
            "provider": guardrail_assessment["provider"],
            "action": guardrail_assessment["action"],
            "topics": guardrail_assessment["topics"],
        },
    }


def send_ask(
    case_id: str,
    *,
    provider: EmailProvider | None = None,
    settings: EmailSettings | None = None,
) -> dict:
    """Send the first outstanding patient-held question."""
    return _send_email(
        case_id,
        allow_resend=False,
        allow_general=False,
        provider=provider,
        settings=settings,
    )


def send_manual_email(
    case_id: str,
    *,
    provider: EmailProvider | None = None,
    settings: EmailSettings | None = None,
) -> dict:
    """Send or resend a patient email from the explicit UI contact control."""
    return _send_email(
        case_id,
        allow_resend=True,
        allow_general=True,
        provider=provider,
        settings=settings,
    )
