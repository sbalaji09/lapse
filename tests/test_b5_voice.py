import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

import httpx

from api.main import app
from engine import store
from engine.loop.inbound import handle_reply
from engine.loop.outbound import send_ask
from engine.loop.voice import (
    TwilioVoiceProvider,
    handle_voice_transcript,
    process_due_voice_calls,
    reconcile_twilio_call,
    record_voice_session_event,
    reminder_message,
    simulate_local_voice_call,
    start_voice_call,
    twilio_status_event,
    verify_twilio_signature,
    voice_case_status,
    voice_settings,
)
from engine.models import Source, VoiceSessionStatus


ROSA_REPLY = "Dejé de trabajar en marzo; solo puedo estar de pie diez minutos."


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.sessions = []

    def start_call(self, session, case):
        self.sessions.append((session, case))
        return {"contact_id": f"contact-{session.attempt}", "status": "connected"}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()


class FakeOpener:
    def __init__(self):
        self.request = None
        self.timeout = None

    def __call__(self, outgoing_request, timeout):
        self.request = outgoing_request
        self.timeout = timeout
        return FakeResponse({"sid": "CA123", "status": "queued"})


class StatusOpener:
    def __init__(self, status):
        self.status = status
        self.request = None

    def __call__(self, outgoing_request, timeout):
        self.request = outgoing_request
        return FakeResponse({"sid": "CA123", "status": self.status})


def prepare_asked_rosa(tmp_path, monkeypatch, asked_at: datetime | None = None):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "voice.sqlite"))
    monkeypatch.setenv("VOICE_BACKEND", "local")
    monkeypatch.setenv("VOICE_CALL_START_HOUR", "0")
    monkeypatch.setenv("VOICE_CALL_END_HOUR", "24")
    store.load_fixtures()
    send_ask("g-rosa")
    if asked_at:
        case = store.get_case("g-rosa")
        event = next(e for e in reversed(case.events) if e["kind"] == "patient_asked")
        event["at"] = asked_at.isoformat()
        store.save_case(case)
    return store.get_case("g-rosa")


def prepare_asked_dummy(tmp_path, monkeypatch):
    prepare_asked_rosa(tmp_path, monkeypatch)
    dummy = store.get_case("g-rosa").model_copy(deep=True)
    dummy.patient_id = "g-dummy"
    dummy.display_name = "Dummy Patient"
    dummy.email = "dummy.patient@example.com"
    dummy.phone = "+1-555-0199"
    for missing in dummy.missing:
        missing.patient_id = dummy.patient_id
        missing.id = missing.id.replace("rosa", "dummy")
    store.save_case(dummy)
    return dummy


def _signature(url: str, params: dict[str, str], token: str) -> str:
    signed = url + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(
        hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()
    ).decode()


def test_legacy_voice_transcript_reuses_inbound_parser(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "voice.sqlite"))
    store.load_fixtures()
    result = handle_voice_transcript(
        "g-rosa",
        ROSA_REPLY,
        recording_ref="demo/voice_rosa.mp3",
        message_id="voice-rosa-demo",
    )
    assert result["parsed"] is True
    assert result["status"] == "waiting_clinician"
    case = store.get_case("g-rosa")
    fact = next(f for f in case.facts if f.key == "standing_tolerance_minutes")
    assert fact.source == Source.patient_reply
    assert fact.source_ref == {
        "message_id": "voice-rosa-demo",
        "channel": "voice",
        "recording_ref": "demo/voice_rosa.mp3",
    }


def test_reminder_call_becomes_due_after_24_hours(tmp_path, monkeypatch):
    asked_at = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
    prepare_asked_rosa(tmp_path, monkeypatch, asked_at)
    settings = replace(
        voice_settings(),
        escalation_minutes=24 * 60,
        timezone_name="America/Los_Angeles",
        call_start_hour=9,
        call_end_hour=18,
    )

    waiting = voice_case_status(
        "g-rosa",
        now=asked_at + timedelta(hours=23, minutes=59),
        settings=settings,
    )
    due = voice_case_status(
        "g-rosa",
        now=asked_at + timedelta(hours=24),
        settings=settings,
    )

    assert waiting["reason"] == "waiting_for_reply"
    assert waiting["due"] is False
    assert due["reason"] == "available"
    assert due["due"] is True
    assert due["will_call_automatically"] is True


def test_scheduler_starts_one_due_call_and_does_not_duplicate_it(tmp_path, monkeypatch):
    asked_at = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
    prepare_asked_rosa(tmp_path, monkeypatch, asked_at)
    settings = replace(
        voice_settings(),
        escalation_minutes=60,
        timezone_name="America/Los_Angeles",
        call_start_hour=9,
        call_end_hour=18,
    )
    provider = FakeProvider()
    now = asked_at + timedelta(hours=1)

    first = process_due_voice_calls(now=now, provider=provider, settings=settings)
    second = process_due_voice_calls(now=now, provider=provider, settings=settings)

    assert len(first["started"]) == 1
    assert first["started"][0]["status"] == "connected"
    assert second["started"] == []
    assert second["skipped"] == [{"case_id": "g-rosa", "reason": "call_in_progress"}]
    assert len(provider.sessions) == 1


def test_scheduler_respects_call_window(tmp_path, monkeypatch):
    asked_at = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)
    prepare_asked_rosa(tmp_path, monkeypatch, asked_at)
    settings = replace(
        voice_settings(),
        escalation_minutes=60,
        timezone_name="America/Los_Angeles",
        call_start_hour=9,
        call_end_hour=18,
    )
    provider = FakeProvider()

    result = process_due_voice_calls(
        now=asked_at + timedelta(hours=1),
        provider=provider,
        settings=settings,
    )

    assert result["started"] == []
    assert result["skipped"] == [{"case_id": "g-rosa", "reason": "outside_call_window"}]
    assert provider.sessions == []


def test_twilio_manual_call_respects_call_window(tmp_path, monkeypatch):
    asked_at = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)
    prepare_asked_rosa(tmp_path, monkeypatch, asked_at)
    settings = replace(
        voice_settings(),
        backend="twilio",
        escalation_minutes=24 * 60,
        timezone_name="America/Los_Angeles",
        call_start_hour=9,
        call_end_hour=18,
    )

    status = voice_case_status(
        "g-rosa",
        now=asked_at + timedelta(hours=1),
        settings=settings,
    )

    assert status["reason"] == "waiting_for_reply"
    assert status["manual_call_allowed"] is False


def test_dummy_patient_call_uses_local_simulator_in_twilio_mode(tmp_path, monkeypatch):
    dummy = prepare_asked_dummy(tmp_path, monkeypatch)
    settings = replace(
        voice_settings(),
        backend="twilio",
        twilio_account_sid="ACtest",
        twilio_auth_token="secret-token",
        twilio_from_phone="+14155550199",
    )

    status = voice_case_status("g-dummy", settings=settings)
    session = start_voice_call("g-dummy", force=True, settings=settings)

    assert status["backend"] == "local"
    assert status["manual_call_allowed"] is True
    assert session.provider == "local"
    assert session.phone == dummy.phone
    assert session.status == VoiceSessionStatus.connected
    completed = simulate_local_voice_call("g-dummy")
    assert completed["session"]["status"] == "completed"


def test_any_email_reply_stops_automatic_calling(tmp_path, monkeypatch):
    asked_at = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
    prepare_asked_rosa(tmp_path, monkeypatch, asked_at)
    handle_reply("g-rosa", "No sé, depende del día.", message_id="email-reply-1")
    case = store.get_case("g-rosa")
    reply_event = next(
        event for event in reversed(case.events) if event["kind"] == "patient_reply_received"
    )
    reply_event["at"] = (asked_at + timedelta(minutes=30)).isoformat()
    store.save_case(case)
    settings = replace(
        voice_settings(),
        escalation_minutes=60,
        timezone_name="America/Los_Angeles",
        call_start_hour=9,
        call_end_hour=18,
    )

    status = voice_case_status(
        "g-rosa",
        now=asked_at + timedelta(hours=2),
        settings=settings,
    )

    assert status["reason"] == "patient_already_replied"
    assert status["will_call_automatically"] is False


def test_completed_reminder_is_idempotent_and_does_not_change_case(tmp_path, monkeypatch):
    case_before = prepare_asked_rosa(tmp_path, monkeypatch)
    session = start_voice_call("g-rosa", force=True, provider=FakeProvider())

    first = record_voice_session_event(session.id, "completed")
    second = record_voice_session_event(session.id, "completed")

    assert first["result"]["delivered"] is True
    assert first["result"]["status"] == "waiting_patient"
    assert first["session"]["status"] == "completed"
    assert second["idempotent"] is True
    case_after = store.get_case("g-rosa")
    assert case_after.status.value == "waiting_patient"
    assert case_after.bucket.value == "ONE_AWAY"
    assert len(case_after.facts) == len(case_before.facts)
    assert voice_case_status("g-rosa")["reason"] == "reminder_delivered"


def test_manual_call_control_can_call_again_after_completed_reminder(tmp_path, monkeypatch):
    prepare_asked_rosa(tmp_path, monkeypatch)
    provider = FakeProvider()
    first = start_voice_call("g-rosa", force=True, provider=provider)
    record_voice_session_event(first.id, "completed")

    status = voice_case_status("g-rosa")
    second = start_voice_call("g-rosa", force=True, provider=provider)

    assert status["reason"] == "reminder_delivered"
    assert status["direct_call_allowed"] is True
    assert second.attempt == 2
    assert second.id.endswith("-2")
    assert len(provider.sessions) == 2


def test_manual_call_control_is_available_for_patient_without_missing_fact(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "voice.sqlite"))
    monkeypatch.setenv("VOICE_BACKEND", "local")
    store.load_fixtures()
    provider = FakeProvider()

    status = voice_case_status("g-linh")
    session = start_voice_call("g-linh", force=True, provider=provider)

    assert status["reason"] == "case_not_one_away"
    assert status["direct_call_allowed"] is True
    assert session.missing_fact_id == "manual-contact-g-linh"
    assert session.status == VoiceSessionStatus.connected


def test_no_answer_waits_for_retry_and_stops_at_attempt_limit(tmp_path, monkeypatch):
    asked_at = datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc)
    prepare_asked_rosa(tmp_path, monkeypatch, asked_at)
    settings = replace(
        voice_settings(),
        escalation_minutes=60,
        retry_minutes=60,
        max_attempts=2,
        timezone_name="America/Los_Angeles",
        call_start_hour=9,
        call_end_hour=18,
    )
    provider = FakeProvider()
    first = start_voice_call(
        "g-rosa",
        now=asked_at + timedelta(hours=1),
        provider=provider,
        settings=settings,
    )
    record_voice_session_event(
        first.id,
        "no_answer",
        now=asked_at + timedelta(hours=1, minutes=1),
    )

    waiting = voice_case_status(
        "g-rosa",
        now=asked_at + timedelta(hours=1, minutes=30),
        settings=settings,
    )
    assert waiting["reason"] == "waiting_for_reply"

    second = start_voice_call(
        "g-rosa",
        now=asked_at + timedelta(hours=2),
        provider=provider,
        settings=settings,
    )
    record_voice_session_event(
        second.id,
        "no_answer",
        now=asked_at + timedelta(hours=2, minutes=1),
    )
    stopped = voice_case_status(
        "g-rosa",
        now=asked_at + timedelta(hours=3),
        settings=settings,
    )
    assert stopped["reason"] == "attempt_limit_reached"
    assert stopped["manual_call_allowed"] is False


def test_scheduler_expires_a_call_that_never_callbacks(tmp_path, monkeypatch):
    asked_at = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
    prepare_asked_rosa(tmp_path, monkeypatch, asked_at)
    settings = replace(
        voice_settings(),
        escalation_minutes=60,
        retry_minutes=60,
        call_timeout_minutes=10,
        timezone_name="America/Los_Angeles",
        call_start_hour=9,
        call_end_hour=18,
    )
    provider = FakeProvider()
    session = start_voice_call(
        "g-rosa",
        now=asked_at + timedelta(hours=1),
        provider=provider,
        settings=settings,
    )

    result = process_due_voice_calls(
        now=asked_at + timedelta(hours=1, minutes=11),
        provider=provider,
        settings=settings,
    )

    assert result["expired"] == [session.id]
    assert store.get_voice_session(session.id).status == VoiceSessionStatus.no_answer
    assert result["started"] == []


def test_twilio_request_uses_fixed_destination_and_inline_reminder(tmp_path, monkeypatch):
    case = prepare_asked_rosa(tmp_path, monkeypatch)
    opener = FakeOpener()
    settings = replace(
        voice_settings(),
        backend="twilio",
        destination_phone="+14155550123",
        twilio_account_sid="ACtest",
        twilio_auth_token="secret-token",
        twilio_from_phone="+14155550199",
        public_api_url="https://example.test",
    )
    provider = TwilioVoiceProvider(settings=settings, opener=opener)

    session = start_voice_call(
        "g-rosa",
        force=True,
        provider=provider,
        settings=settings,
    )

    form = parse_qs(opener.request.data.decode())
    assert session.status == VoiceSessionStatus.dialing
    assert session.phone == "+14155550123"
    assert form["To"] == ["+14155550123"]
    assert form["To"] != [case.phone]
    assert form["From"] == ["+14155550199"]
    assert session.language == "es"
    assert reminder_message("es", "Rosa") in form["Twiml"][0]
    assert "Siddharth" not in form["Twiml"][0]
    assert 'language="es-MX"' in form["Twiml"][0]
    assert "¿Cuánto tiempo" not in form["Twiml"][0]
    assert "standing_tolerance" not in form["Twiml"][0]
    assert form["StatusCallback"] == [
        f"https://example.test/api/voice/twilio/status/{session.id}"
    ]
    assert form["StatusCallbackEvent"] == [
        "initiated",
        "ringing",
        "answered",
        "completed",
    ]
    assert opener.request.get_header("Authorization").startswith("Basic ")
    assert "secret-token" not in opener.request.data.decode()
    assert opener.timeout == 15


def test_twilio_polling_marks_hung_up_call_complete_without_public_callback(tmp_path, monkeypatch):
    prepare_asked_rosa(tmp_path, monkeypatch)
    create_opener = FakeOpener()
    status_opener = StatusOpener("completed")
    settings = replace(
        voice_settings(),
        backend="twilio",
        destination_phone="+14155550123",
        twilio_account_sid="ACtest",
        twilio_auth_token="secret-token",
        twilio_from_phone="+14155550199",
        public_api_url=None,
    )
    provider = TwilioVoiceProvider(settings=settings, opener=create_opener)
    session = start_voice_call(
        "g-rosa",
        force=True,
        provider=provider,
        settings=settings,
    )

    result = reconcile_twilio_call(
        "g-rosa",
        settings=settings,
        opener=status_opener,
    )

    assert result["updated"] is True
    assert result["session"]["status"] == "completed"
    assert store.get_voice_session(session.id).status == VoiceSessionStatus.completed
    assert status_opener.request.get_method() == "GET"
    assert status_opener.request.get_header("Authorization").startswith("Basic ")


def test_twilio_signature_validation():
    url = "https://example.test/api/voice/twilio/status/voice-1"
    params = {"CallSid": "CA123", "CallStatus": "completed"}
    signature = _signature(url, params, "token")

    assert verify_twilio_signature(url, params, signature, "token") is True
    assert verify_twilio_signature(url, params, "wrong", "token") is False


def test_twilio_status_mapping_and_call_sid_check(tmp_path, monkeypatch):
    prepare_asked_rosa(tmp_path, monkeypatch)
    session = start_voice_call("g-rosa", force=True, provider=FakeProvider())

    connected = twilio_status_event(
        session.id,
        "in-progress",
        call_sid=session.provider_contact_id,
    )
    completed = twilio_status_event(
        session.id,
        "completed",
        call_sid=session.provider_contact_id,
    )

    assert connected["session"]["status"] == "connected"
    assert completed["session"]["status"] == "completed"
    assert completed["result"]["delivered"] is True


def test_twilio_callback_rejects_bad_signature_and_is_idempotent(tmp_path, monkeypatch):
    prepare_asked_rosa(tmp_path, monkeypatch)
    session = start_voice_call("g-rosa", force=True, provider=FakeProvider())
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "callback-token")
    monkeypatch.setenv("LAPSE_PUBLIC_API_URL", "https://example.test")
    callback_url = f"https://example.test/api/voice/twilio/status/{session.id}"
    params = {
        "CallStatus": "completed",
        "CallSid": session.provider_contact_id,
    }
    signature = _signature(callback_url, params, "callback-token")

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            rejected = await client.post(
                f"/api/voice/twilio/status/{session.id}",
                content=urlencode(params),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Twilio-Signature": "bad",
                },
            )
            first = await client.post(
                f"/api/voice/twilio/status/{session.id}",
                content=urlencode(params),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Twilio-Signature": signature,
                },
            )
            second = await client.post(
                f"/api/voice/twilio/status/{session.id}",
                content=urlencode(params),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Twilio-Signature": signature,
                },
            )
            return rejected, first, second

    rejected, first, second = asyncio.run(run())

    assert rejected.status_code == 401
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("application/xml")
    assert second.status_code == 200
    case = store.get_case("g-rosa")
    assert sum(event["kind"] == "voice_call_completed" for event in case.events) == 1


def test_voice_api_local_reminder_then_email_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "voice-api.sqlite"))
    monkeypatch.setenv("VOICE_BACKEND", "local")
    store.load_fixtures()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ask = await client.post("/api/cases/g-rosa/ask")
            call = await client.post("/api/cases/g-rosa/voice/call")
            complete = await client.post(
                "/api/cases/g-rosa/voice/simulate",
                json={"outcome": "completed"},
            )
            waiting = await client.get("/api/cases/g-rosa")
            reply = await client.post(
                "/api/cases/g-rosa/reply",
                json={"text": ROSA_REPLY},
            )
            resolved = await client.get("/api/cases/g-rosa")
            return ask, call, complete, waiting, reply, resolved

    ask, call, complete, waiting, reply, resolved = asyncio.run(run())

    assert ask.status_code == 200
    assert call.status_code == 200
    assert call.json()["status"] == "connected"
    assert complete.status_code == 200
    assert complete.json()["result"]["delivered"] is True
    assert waiting.json()["voice"]["latest_session"]["status"] == "completed"
    assert waiting.json()["voice"]["reason"] == "reminder_delivered"
    assert waiting.json()["status"] == "waiting_patient"
    assert waiting.json()["bucket"] == "ONE_AWAY"
    assert reply.status_code == 200
    assert resolved.json()["status"] == "waiting_clinician"
    assert resolved.json()["bucket"] == "PROVABLE"
