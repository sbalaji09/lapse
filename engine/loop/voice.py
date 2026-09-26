"""Proactive one-way reminder calls through Twilio or the local simulator.

The call never collects clinical information. It reads a fixed reminder asking
the recipient to check their email, then hangs up. The existing email reply
handler remains the only patient response path.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import ssl
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Callable, Mapping, Protocol
from urllib import error, parse, request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import certifi

from engine import store
from engine.config import DEMO_ROSA_PHONE
from engine.loop.inbound import handle_reply
from engine.models import Bucket, Case, Holder, VoiceSession, VoiceSessionStatus

ACTIVE_STATUSES = {VoiceSessionStatus.dialing, VoiceSessionStatus.connected}
TERMINAL_STATUSES = {
    VoiceSessionStatus.completed,
    VoiceSessionStatus.declined,
    VoiceSessionStatus.cancelled,
    VoiceSessionStatus.needs_human,
    VoiceSessionStatus.failed,
    VoiceSessionStatus.no_answer,
}
E164 = re.compile(r"^\+[1-9]\d{1,14}$")

REMINDER_MESSAGES = {
    "en": (
        "Hi {name}, this is the Lapse voice assistant. "
        "This is a friendly reminder from your care team. "
        "Please check your email and respond to any message from your care team when you can. Thanks."
    ),
    "es": (
        "Hola {name}, este es el asistente de voz de Lapse. "
        "Este es un recordatorio amistoso de su equipo de atención. "
        "Por favor revise su correo electrónico y responda a cualquier mensaje de su equipo de atención. "
        "Si necesita ayuda, llame a su clínica. Gracias."
    ),
}


def _secure_urlopen(outgoing_request, timeout: int):
    context = ssl.create_default_context(cafile=certifi.where())
    return request.urlopen(outgoing_request, timeout=timeout, context=context)


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class VoiceSettings:
    backend: str
    automation_enabled: bool
    escalation_minutes: int
    retry_minutes: int
    max_attempts: int
    call_timeout_minutes: int
    timezone_name: str
    call_start_hour: int
    call_end_hour: int
    scheduler_interval_seconds: int
    message_language: str
    destination_phone: str | None
    twilio_account_sid: str | None
    twilio_auth_token: str | None
    twilio_from_phone: str | None
    public_api_url: str | None


def voice_settings() -> VoiceSettings:
    return VoiceSettings(
        backend=os.environ.get("VOICE_BACKEND", "local").strip().lower(),
        automation_enabled=_bool_env("VOICE_AUTOMATION_ENABLED", True),
        escalation_minutes=_int_env("VOICE_ESCALATION_MINUTES", 24 * 60, 0),
        retry_minutes=_int_env("VOICE_RETRY_MINUTES", 24 * 60, 1),
        max_attempts=_int_env("VOICE_MAX_ATTEMPTS", 2, 1),
        call_timeout_minutes=_int_env("VOICE_CALL_TIMEOUT_MINUTES", 10, 1),
        timezone_name=os.environ.get("VOICE_TIMEZONE", "America/Los_Angeles"),
        call_start_hour=min(23, _int_env("VOICE_CALL_START_HOUR", 9, 0)),
        call_end_hour=min(24, _int_env("VOICE_CALL_END_HOUR", 18, 1)),
        scheduler_interval_seconds=_int_env("VOICE_SCHEDULER_INTERVAL_SECONDS", 60, 5),
        message_language=os.environ.get("VOICE_MESSAGE_LANGUAGE", "en").strip().lower(),
        destination_phone=(
            os.environ.get("VOICE_DESTINATION_PHONE") or DEMO_ROSA_PHONE
        ),
        twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID") or None,
        twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN") or None,
        twilio_from_phone=os.environ.get("TWILIO_FROM_PHONE") or None,
        public_api_url=(os.environ.get("LAPSE_PUBLIC_API_URL") or "").rstrip("/") or None,
    )


def reminder_message(language: str, name: str = "there") -> str:
    template = REMINDER_MESSAGES.get(language, REMINDER_MESSAGES["en"])
    return template.format(name=name)


def configuration_errors(settings: VoiceSettings | None = None) -> list[str]:
    settings = settings or voice_settings()
    errors: list[str] = []
    if settings.backend not in {"local", "twilio"}:
        errors.append("VOICE_BACKEND must be local or twilio")
    try:
        ZoneInfo(settings.timezone_name)
    except ZoneInfoNotFoundError:
        errors.append(f"unknown VOICE_TIMEZONE: {settings.timezone_name}")
    if settings.call_start_hour >= settings.call_end_hour:
        errors.append("VOICE_CALL_START_HOUR must be earlier than VOICE_CALL_END_HOUR")
    if settings.message_language not in REMINDER_MESSAGES:
        errors.append("VOICE_MESSAGE_LANGUAGE must be en or es")
    if settings.destination_phone and not E164.fullmatch(settings.destination_phone):
        errors.append("VOICE_DESTINATION_PHONE must use E.164 format, for example +14155550123")
    if settings.backend == "twilio":
        required = {
            "VOICE_DESTINATION_PHONE": settings.destination_phone,
            "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
            "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
            "TWILIO_FROM_PHONE": settings.twilio_from_phone,
        }
        errors.extend(
            f"{name} is required for VOICE_BACKEND=twilio"
            for name, value in required.items()
            if not value
        )
        if settings.twilio_from_phone and not E164.fullmatch(settings.twilio_from_phone):
            errors.append("TWILIO_FROM_PHONE must use E.164 format, for example +14155550123")
    return errors


def voice_health() -> dict:
    settings = voice_settings()
    errors = configuration_errors(settings)
    return {
        "configured": not errors,
        "backend": settings.backend,
        "automation_enabled": settings.automation_enabled,
        "escalation_minutes": settings.escalation_minutes,
        "retry_minutes": settings.retry_minutes,
        "max_attempts": settings.max_attempts,
        "call_timeout_minutes": settings.call_timeout_minutes,
        "destination_configured": bool(settings.destination_phone),
        "call_window": {
            "timezone": settings.timezone_name,
            "start_hour": settings.call_start_hour,
            "end_hour": settings.call_end_hour,
        },
        "errors": errors,
    }


def handle_voice_transcript(
    case_id: str,
    transcript: str,
    recording_ref: str | None = None,
    message_id: str | None = None,
) -> dict:
    """Legacy seam retained for old recordings; reminder calls do not use it."""
    ref = {"channel": "voice"}
    if recording_ref:
        ref["recording_ref"] = recording_ref
    return handle_reply(
        case_id,
        transcript,
        message_id or f"voice-{case_id}",
        source_ref=ref,
    )


def _now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_time(event: dict) -> datetime | None:
    raw = event.get("at")
    if not isinstance(raw, str):
        return None
    try:
        return _now(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def _top_patient_missing(case: Case):
    return next(
        (
            item
            for item in case.missing
            if item.holder == Holder.patient and item.status in {"open", "asked"}
        ),
        None,
    )


def _last_email_time(case: Case) -> datetime | None:
    times = [
        parsed
        for event in case.events
        if event.get("kind") in {"patient_asked", "patient_email_sent"}
        for parsed in [_event_time(event)]
        if parsed is not None
    ]
    return max(times) if times else None


def _last_non_voice_reply_time(case: Case, voice_session_ids: set[str]) -> datetime | None:
    times = []
    for event in case.events:
        if event.get("kind") != "patient_reply_received":
            continue
        message_id = (event.get("detail") or {}).get("message_id")
        if message_id in voice_session_ids:
            continue
        parsed = _event_time(event)
        if parsed is not None:
            times.append(parsed)
    return max(times) if times else None


def _inside_call_window(now: datetime, settings: VoiceSettings) -> bool:
    try:
        local = now.astimezone(ZoneInfo(settings.timezone_name))
    except ZoneInfoNotFoundError:
        return False
    return settings.call_start_hour <= local.hour < settings.call_end_hour


def _public_session(session: VoiceSession | None) -> dict | None:
    return session.model_dump(mode="json") if session else None


def _case_voice_backend(case: Case, settings: VoiceSettings) -> str:
    email_domain = case.email.rsplit("@", 1)[-1].lower() if "@" in case.email else ""
    if case.patient_id != "g-rosa" and (
        email_domain == "example.com" or email_domain.endswith(".example.com")
    ):
        return "local"
    return settings.backend


def _reminder_name(case: Case) -> str:
    return case.display_name.split()[0]


def _reminder_language(case: Case, settings: VoiceSettings) -> str:
    return case.language if case.language in REMINDER_MESSAGES else settings.message_language


def voice_case_status(
    case_id: str,
    now: datetime | None = None,
    settings: VoiceSettings | None = None,
) -> dict:
    settings = settings or voice_settings()
    now = _now(now)
    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"no such case: {case_id}")
    effective_backend = _case_voice_backend(case, settings)
    effective_settings = replace(settings, backend=effective_backend)
    if effective_backend == "twilio":
        reconcile_twilio_call(case_id, settings=effective_settings, now=now)

    all_sessions = store.list_voice_sessions(case_id=case_id)
    missing = _top_patient_missing(case)
    sessions = (
        [session for session in all_sessions if session.missing_fact_id == missing.id]
        if missing
        else all_sessions
    )
    latest = all_sessions[-1] if all_sessions else None
    latest_for_fact = sessions[-1] if sessions else None
    asked_at = _last_email_time(case)
    replied_at = _last_non_voice_reply_time(case, {session.id for session in all_sessions})
    reason = "available"
    due_at: datetime | None = None

    if case.bucket != Bucket.ONE_AWAY:
        reason = "case_not_one_away"
    elif missing is None:
        reason = "no_open_patient_fact"
    elif missing.status != "asked":
        reason = "email_not_sent"
    elif any(session.status == VoiceSessionStatus.declined for session in sessions):
        reason = "patient_declined"
    elif any(session.status == VoiceSessionStatus.completed for session in sessions):
        reason = "reminder_delivered"
    elif any(session.status == VoiceSessionStatus.needs_human for session in sessions):
        reason = "answer_already_received"
    elif latest_for_fact and latest_for_fact.status in ACTIVE_STATUSES:
        reason = "call_in_progress"
    elif asked_at and replied_at and replied_at >= asked_at:
        reason = "patient_already_replied"
    elif len(sessions) >= settings.max_attempts:
        reason = "attempt_limit_reached"
    elif asked_at is None:
        reason = "email_not_sent"
    else:
        due_at = asked_at + timedelta(minutes=settings.escalation_minutes)
        if latest_for_fact:
            retry_at = latest_for_fact.created_at + timedelta(minutes=settings.retry_minutes)
            due_at = max(due_at, retry_at)
        if now < due_at:
            reason = "waiting_for_reply"
        elif not _inside_call_window(now, settings):
            reason = "outside_call_window"

    manual_call_allowed = (
        effective_backend == "local"
        and reason in {"available", "waiting_for_reply", "outside_call_window"}
    ) or (
        effective_backend == "twilio"
        and reason in {"available", "waiting_for_reply"}
        and _inside_call_window(now, settings)
    )
    direct_call_allowed = (
        not any(session.status in ACTIVE_STATUSES for session in all_sessions)
        and not configuration_errors(effective_settings)
    )
    due = reason == "available"
    errors = configuration_errors(effective_settings)
    return {
        "backend": effective_backend,
        "automation_enabled": settings.automation_enabled,
        "configured": not errors,
        "configuration_errors": errors,
        "escalation_minutes": settings.escalation_minutes,
        "max_attempts": settings.max_attempts,
        "attempts": len(sessions),
        "reason": reason,
        "due": due,
        "due_at": due_at.isoformat() if due_at else None,
        "manual_call_allowed": manual_call_allowed,
        "direct_call_allowed": direct_call_allowed,
        "will_call_automatically": (
            settings.automation_enabled
            and reason in {"available", "waiting_for_reply", "outside_call_window"}
        ),
        "call_window": {
            "timezone": settings.timezone_name,
            "start_hour": settings.call_start_hour,
            "end_hour": settings.call_end_hour,
        },
        "latest_session": _public_session(latest),
        "reminder_message": reminder_message(
            _reminder_language(case, settings),
            _reminder_name(case),
        ),
    }


class VoiceProvider(Protocol):
    name: str

    def start_call(self, session: VoiceSession, case: Case) -> dict:
        ...


class LocalVoiceProvider:
    name = "local"

    def start_call(self, session: VoiceSession, case: Case) -> dict:
        return {
            "contact_id": f"local-{session.id}",
            "status": VoiceSessionStatus.connected.value,
        }


class TwilioVoiceProvider:
    name = "twilio"

    def __init__(
        self,
        settings: VoiceSettings | None = None,
        opener: Callable | None = None,
    ):
        self.settings = settings or voice_settings()
        errors = configuration_errors(self.settings)
        if errors:
            raise RuntimeError("; ".join(errors))
        self.opener = opener or _secure_urlopen

    def start_call(self, session: VoiceSession, case: Case) -> dict:
        settings = self.settings
        language = "es-MX" if session.language == "es" else "en-US"
        twiml = (
            f'<Response><Say language="{language}">{escape(session.message)}</Say>'
            "<Pause length=\"1\"/><Hangup/></Response>"
        )
        fields = [
            ("To", session.phone),
            ("From", settings.twilio_from_phone),
            ("Twiml", twiml),
        ]
        if settings.public_api_url:
            callback_url = (
                f"{settings.public_api_url}/api/voice/twilio/status/{parse.quote(session.id)}"
            )
            fields.extend(
                [
                    ("StatusCallback", callback_url),
                    ("StatusCallbackMethod", "POST"),
                    ("StatusCallbackEvent", "initiated"),
                    ("StatusCallbackEvent", "ringing"),
                    ("StatusCallbackEvent", "answered"),
                    ("StatusCallbackEvent", "completed"),
                ]
            )
        endpoint = (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{parse.quote(settings.twilio_account_sid)}/Calls.json"
        )
        credentials = base64.b64encode(
            f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode()
        ).decode()
        call_request = request.Request(
            endpoint,
            data=parse.urlencode(fields).encode(),
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "lapse-reminder-call/1.0",
            },
            method="POST",
        )
        try:
            response = self.opener(call_request, timeout=15)
            payload = json.loads(response.read().decode())
        except error.HTTPError as exc:
            raise RuntimeError(f"Twilio call request failed with HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Twilio call request failed") from exc
        if not payload.get("sid"):
            raise RuntimeError("Twilio response did not include a call SID")
        return {
            "contact_id": payload["sid"],
            "status": _twilio_session_status(payload.get("status", "queued")).value,
        }


def _provider(settings: VoiceSettings) -> VoiceProvider:
    if settings.backend == "local":
        return LocalVoiceProvider()
    if settings.backend == "twilio":
        return TwilioVoiceProvider(settings)
    raise RuntimeError(f"unsupported voice backend: {settings.backend}")


def _safe_error(exc: Exception, phone: str) -> str:
    return str(exc).replace(phone, "[redacted phone]")[:600]


def start_voice_call(
    case_id: str,
    *,
    force: bool = False,
    now: datetime | None = None,
    provider: VoiceProvider | None = None,
    settings: VoiceSettings | None = None,
) -> VoiceSession:
    settings = settings or voice_settings()
    now = _now(now)
    status = voice_case_status(case_id, now=now, settings=settings)
    if force:
        if not status["direct_call_allowed"] and provider is None:
            raise ValueError(f"voice call unavailable: {status['reason']}")
        if status["latest_session"] and status["latest_session"]["status"] in {
            item.value for item in ACTIVE_STATUSES
        }:
            raise ValueError("voice call unavailable: call_in_progress")
    else:
        if not status["manual_call_allowed"]:
            raise ValueError(f"voice call unavailable: {status['reason']}")
        if not status["due"]:
            raise ValueError(f"voice call is not due: {status['reason']}")
    effective_settings = replace(settings, backend=status["backend"])
    if provider is None:
        errors = configuration_errors(effective_settings)
        if errors:
            raise RuntimeError("; ".join(errors))

    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"no such case: {case_id}")
    missing = _top_patient_missing(case)
    if missing is None and not force:
        raise ValueError(f"voice call unavailable for case: {case_id}")
    contact_key = missing.id if missing else f"manual-contact-{case_id}"

    selected_provider = provider or _provider(effective_settings)
    attempt = 1 + sum(
        session.missing_fact_id == contact_key
        for session in store.list_voice_sessions(case_id=case_id)
    )
    due_at = datetime.fromisoformat(status["due_at"]) if status["due_at"] else now
    destination = (
        settings.destination_phone
        if case.patient_id == "g-rosa"
        else case.phone
    ) or "local-simulator"
    if selected_provider.name == "twilio" and not E164.fullmatch(destination):
        raise ValueError("voice call unavailable: phone must use E.164 format")
    session = VoiceSession(
        id=f"voice-{contact_key}-{attempt}",
        case_id=case_id,
        missing_fact_id=contact_key,
        provider=selected_provider.name,
        status=VoiceSessionStatus.dialing,
        attempt=attempt,
        phone=destination,
        language=_reminder_language(case, settings),
        message=reminder_message(_reminder_language(case, settings), _reminder_name(case)),
        created_at=now,
        updated_at=now,
        due_at=due_at,
    )
    if not store.create_voice_session(session):
        existing = store.get_voice_session(session.id)
        if existing is None:
            raise RuntimeError("voice attempt could not be claimed")
        return existing

    try:
        started = selected_provider.start_call(session, case)
    except Exception as exc:
        session.status = VoiceSessionStatus.failed
        session.updated_at = now
        session.error = _safe_error(exc, session.phone)
        store.save_voice_session(session)
        case.events.append(
            {
                "at": now.isoformat(),
                "kind": "voice_call_failed",
                "detail": {
                    "session_id": session.id,
                    "attempt": session.attempt,
                    "error": session.error,
                },
            }
        )
        store.save_case(case)
        raise RuntimeError(session.error) from exc

    session.provider_contact_id = started.get("contact_id")
    session.status = VoiceSessionStatus(
        started.get("status", VoiceSessionStatus.dialing.value)
    )
    session.updated_at = now
    store.save_voice_session(session)
    case.events.append(
        {
            "at": now.isoformat(),
            "kind": "voice_call_started",
            "detail": {
                "session_id": session.id,
                "attempt": session.attempt,
                "provider": session.provider,
                "language": session.language,
                "manual": force,
            },
        }
    )
    store.save_case(case)
    return session


def record_voice_session_event(
    session_id: str,
    status: str,
    *,
    error_message: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = _now(now)
    session = store.get_voice_session(session_id)
    if session is None:
        raise ValueError(f"no such voice session: {session_id}")

    requested = VoiceSessionStatus(status)
    if session.status in TERMINAL_STATUSES:
        return {
            "session": _public_session(session),
            "result": session.result,
            "idempotent": True,
        }
    if session.status == VoiceSessionStatus.connected and requested == VoiceSessionStatus.dialing:
        return {
            "session": _public_session(session),
            "result": session.result,
            "idempotent": True,
        }

    case = store.get_case(session.case_id)
    if case is None:
        raise ValueError(f"no such case: {session.case_id}")

    session.status = requested
    session.error = error_message[:600] if error_message else None
    if requested == VoiceSessionStatus.completed:
        session.result = {
            "delivered": True,
            "status": case.status.value,
            "bucket": case.bucket.value,
        }
        event_kind = "voice_call_completed"
        detail = {
            "session_id": session.id,
            "attempt": session.attempt,
            "delivered": True,
        }
    else:
        event_kind = {
            VoiceSessionStatus.connected: "voice_call_connected",
            VoiceSessionStatus.no_answer: "voice_call_no_answer",
            VoiceSessionStatus.declined: "voice_call_declined",
            VoiceSessionStatus.cancelled: "voice_call_cancelled",
            VoiceSessionStatus.needs_human: "voice_call_needs_human",
            VoiceSessionStatus.failed: "voice_call_failed",
            VoiceSessionStatus.dialing: "voice_call_dialing",
        }[requested]
        detail = {
            "session_id": session.id,
            "attempt": session.attempt,
            "error": session.error,
        }

    session.updated_at = now
    store.save_voice_session(session)
    case.events.append({"at": now.isoformat(), "kind": event_kind, "detail": detail})
    store.save_case(case)
    return {
        "session": _public_session(session),
        "result": session.result,
        "idempotent": False,
    }


def simulate_local_voice_call(
    case_id: str,
    *,
    outcome: str = "completed",
    now: datetime | None = None,
) -> dict:
    active = next(
        (
            session
            for session in reversed(store.list_voice_sessions(case_id=case_id))
            if session.status in ACTIVE_STATUSES
        ),
        None,
    )
    if active is None:
        raise ValueError(f"no active voice session for case: {case_id}")
    if active.provider != "local":
        raise ValueError("voice simulation is available only for a local call")
    return record_voice_session_event(active.id, outcome, now=now)


def expire_stale_voice_sessions(
    *,
    now: datetime | None = None,
    settings: VoiceSettings | None = None,
) -> list[str]:
    settings = settings or voice_settings()
    now = _now(now)
    expired: list[str] = []
    for status in ACTIVE_STATUSES:
        for session in store.list_voice_sessions(status=status.value):
            if session.created_at + timedelta(minutes=settings.call_timeout_minutes) > now:
                continue
            record_voice_session_event(
                session.id,
                VoiceSessionStatus.no_answer.value,
                error_message="call ended without a completion callback",
                now=now,
            )
            expired.append(session.id)
    return expired


def _twilio_session_status(status: str) -> VoiceSessionStatus:
    normalized = status.strip().lower()
    if normalized in {"queued", "initiated", "ringing"}:
        return VoiceSessionStatus.dialing
    if normalized in {"answered", "in-progress"}:
        return VoiceSessionStatus.connected
    if normalized == "completed":
        return VoiceSessionStatus.completed
    if normalized in {"busy", "no-answer"}:
        return VoiceSessionStatus.no_answer
    if normalized == "canceled":
        return VoiceSessionStatus.cancelled
    return VoiceSessionStatus.failed


def reconcile_twilio_call(
    case_id: str,
    *,
    settings: VoiceSettings | None = None,
    now: datetime | None = None,
    opener: Callable | None = None,
) -> dict | None:
    """Refresh an active Twilio call when callbacks are unavailable or delayed."""
    settings = settings or voice_settings()
    active = next(
        (
            session
            for session in reversed(store.list_voice_sessions(case_id=case_id))
            if session.provider == "twilio"
            and session.provider_contact_id
            and session.status in ACTIVE_STATUSES
        ),
        None,
    )
    if active is None or not settings.twilio_account_sid or not settings.twilio_auth_token:
        return None

    endpoint = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{parse.quote(settings.twilio_account_sid)}/Calls/"
        f"{parse.quote(active.provider_contact_id)}.json"
    )
    credentials = base64.b64encode(
        f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode()
    ).decode()
    status_request = request.Request(
        endpoint,
        headers={
            "Authorization": f"Basic {credentials}",
            "User-Agent": "lapse-reminder-call/1.0",
        },
        method="GET",
    )
    try:
        response = (opener or _secure_urlopen)(status_request, timeout=10)
        payload = json.loads(response.read().decode())
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    provider_status = payload.get("status")
    if not provider_status:
        return None
    mapped = _twilio_session_status(provider_status)
    if mapped == active.status:
        return {"session": _public_session(active), "updated": False}
    result = record_voice_session_event(
        active.id,
        mapped.value,
        error_message=(
            f"Twilio error {payload['error_code']}"
            if payload.get("error_code")
            else None
        ),
        now=now,
    )
    return {**result, "updated": True}


def verify_twilio_signature(
    url: str,
    params: Mapping[str, str | list[str]],
    signature: str | None,
    auth_token: str | None = None,
) -> bool:
    token = auth_token or voice_settings().twilio_auth_token
    if not token or not signature:
        return False
    signed = url
    for key in sorted(params):
        values = params[key]
        for value in values if isinstance(values, list) else [values]:
            signed += key + value
    digest = base64.b64encode(
        hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(digest, signature)


def twilio_status_event(
    session_id: str,
    provider_status: str,
    *,
    call_sid: str | None = None,
    error_code: str | None = None,
    now: datetime | None = None,
) -> dict:
    session = store.get_voice_session(session_id)
    if session is None:
        raise ValueError(f"no such voice session: {session_id}")
    if call_sid and session.provider_contact_id and call_sid != session.provider_contact_id:
        raise ValueError("Twilio call SID does not match the voice session")
    mapped = _twilio_session_status(provider_status)
    return record_voice_session_event(
        session_id,
        mapped.value,
        error_message=f"Twilio error {error_code}" if error_code else None,
        now=now,
    )


def process_due_voice_calls(
    *,
    now: datetime | None = None,
    provider: VoiceProvider | None = None,
    settings: VoiceSettings | None = None,
) -> dict:
    settings = settings or voice_settings()
    now = _now(now)
    if not settings.automation_enabled:
        return {"enabled": False, "expired": [], "started": [], "skipped": [], "errors": []}
    expired = expire_stale_voice_sessions(now=now, settings=settings)
    started: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    for case in store.list_cases(status="waiting_patient"):
        status = voice_case_status(case.patient_id, now=now, settings=settings)
        if not status["due"]:
            skipped.append({"case_id": case.patient_id, "reason": status["reason"]})
            continue
        try:
            session = start_voice_call(
                case.patient_id,
                now=now,
                provider=provider,
                settings=settings,
            )
            started.append(_public_session(session))
        except (RuntimeError, ValueError) as exc:
            errors.append({"case_id": case.patient_id, "error": str(exc)})
    return {
        "enabled": True,
        "expired": expired,
        "started": started,
        "skipped": skipped,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "process-due"))
    args = parser.parse_args()
    if args.command == "check":
        health = voice_health()
        for key, value in health.items():
            print(f"{key}: {value}")
        raise SystemExit(0 if health["configured"] else 1)
    print(process_due_voice_calls())


if __name__ == "__main__":
    main()
