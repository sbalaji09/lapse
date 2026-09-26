import json
from dataclasses import replace

import pytest

from engine import guardrails, store
from engine.loop import outbound
from engine.loop.outbound import (
    ResendEmailProvider,
    email_health,
    email_settings,
    send_ask,
    send_manual_email,
)


class FakeResponse:
    def read(self):
        return json.dumps({"id": "email-resend-123"}).encode()


class FakeOpener:
    def __init__(self):
        self.request = None
        self.timeout = None

    def __call__(self, outgoing_request, timeout):
        self.request = outgoing_request
        self.timeout = timeout
        return FakeResponse()


class FailingProvider:
    name = "failing"

    def send(self, **kwargs):
        raise RuntimeError("provider unavailable")


class RecordingProvider:
    name = "recording"

    def __init__(self):
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return {"provider_message_id": "recorded-1", "delivery": "sent"}


def prepare_rosa(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "email.sqlite"))
    monkeypatch.delenv("DEMO_INBOX", raising=False)
    store.load_fixtures()
    return store.get_case("g-rosa")


def add_dummy_patient_case():
    dummy = store.get_case("g-rosa").model_copy(deep=True)
    dummy.patient_id = "g-dummy"
    dummy.display_name = "Dummy Patient"
    dummy.email = "dummy.patient@example.com"
    dummy.phone = "+1-555-0199"
    for missing in dummy.missing:
        missing.patient_id = dummy.patient_id
        missing.id = missing.id.replace("rosa", "dummy")
        missing.status = "open"
    store.save_case(dummy)
    return dummy


def test_rosa_uses_the_configured_real_contact_details(tmp_path, monkeypatch):
    rosa = prepare_rosa(tmp_path, monkeypatch)

    assert rosa.email == "siddharthbalaji6@gmail.com"
    assert rosa.phone == "+14086100377"

    result = send_ask("g-rosa")

    assert result["provider"] == "local"
    assert result["delivery"] == "preview"
    assert result["to"] == "siddharthbalaji6@gmail.com"


def test_resend_request_contains_rendered_email_and_no_key_in_body(tmp_path, monkeypatch):
    prepare_rosa(tmp_path, monkeypatch)
    opener = FakeOpener()
    settings = replace(
        email_settings(),
        backend="resend",
        resend_api_key="re_test_secret",
        from_email="Lapse <onboarding@resend.dev>",
        timeout_seconds=9,
    )
    provider = ResendEmailProvider(settings=settings, opener=opener)

    result = send_ask("g-rosa", provider=provider, settings=settings)

    payload = json.loads(opener.request.data)
    assert opener.request.full_url == "https://api.resend.com/emails"
    assert opener.request.get_method() == "POST"
    assert opener.request.get_header("Authorization") == "Bearer re_test_secret"
    assert opener.request.get_header("Idempotency-key") == "msg-g-rosa-m-rosa-1"
    assert opener.timeout == 9
    assert payload["from"] == "Lapse <onboarding@resend.dev>"
    assert payload["to"] == ["siddharthbalaji6@gmail.com"]
    assert "¿Cuánto tiempo" in payload["text"]
    assert "re_test_secret" not in opener.request.data.decode()
    assert result["provider_message_id"] == "email-resend-123"
    assert result["delivery"] == "sent"


def test_send_failure_does_not_mark_the_question_as_sent(tmp_path, monkeypatch):
    prepare_rosa(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        send_ask("g-rosa", provider=FailingProvider())

    rosa = store.get_case("g-rosa")
    assert rosa.status.value == "needs_action"
    assert rosa.missing[0].status == "open"
    assert not any(event["kind"] == "patient_asked" for event in rosa.events)


def test_resend_becomes_default_when_api_key_is_present(monkeypatch):
    monkeypatch.delenv("EMAIL_BACKEND", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret")

    health = email_health()

    assert health["backend"] == "resend"
    assert health["configured"] is True


def test_demo_reset_preserves_rosa_contact_override(tmp_path, monkeypatch):
    rosa = prepare_rosa(tmp_path, monkeypatch)
    rosa.email = "old@example.com"
    rosa.phone = "+15550100101"
    store.save_baseline([rosa])
    store.reset_demo()

    restored = store.get_case("g-rosa")

    assert restored.email == "siddharthbalaji6@gmail.com"
    assert restored.phone == "+14086100377"


def test_privileged_message_is_blocked_before_delivery_or_state_change(tmp_path, monkeypatch):
    prepare_rosa(tmp_path, monkeypatch)
    provider = RecordingProvider()
    monkeypatch.setattr(
        outbound,
        "render_email",
        lambda case, missing: {
            "subject": "Your case status",
            "body": "Rosa, you are exempt and your case status is ONE_AWAY.",
        },
    )

    with pytest.raises(guardrails.GuardrailIntervened):
        send_ask("g-rosa", provider=provider)

    assert provider.calls == []
    rosa = store.get_case("g-rosa")
    assert rosa.status.value == "needs_action"
    assert rosa.missing[0].status == "open"
    assert not any(event["kind"] == "patient_asked" for event in rosa.events)


@pytest.mark.parametrize(
    "message",
    [
        "Rosa, you are exempt from the work requirement.",
        "Your renewal status is pending.",
        "Rosa's status is ONE_AWAY.",
        "El estado de su renovación está pendiente.",
        "You will keep your Medi-Cal coverage.",
    ],
)
def test_local_guardrail_blocks_privileged_patient_language(message):
    assessment = guardrails.check_local_patient_message(message)

    assert assessment["allowed"] is False
    assert assessment["action"] == "GUARDRAIL_INTERVENED"


def test_configured_bedrock_guardrail_checks_rendered_email_before_send(tmp_path, monkeypatch):
    prepare_rosa(tmp_path, monkeypatch)
    provider = RecordingProvider()
    checked = []
    monkeypatch.setattr(guardrails, "configured", lambda: True)

    def allow_message(text):
        checked.append(text)
        return {
            "allowed": True,
            "action": "NONE",
            "output": "",
            "topics": [],
            "provider": "Amazon Bedrock Guardrails",
        }

    monkeypatch.setattr(guardrails, "check_patient_message", allow_message)

    result = send_ask("g-rosa", provider=provider)

    assert len(checked) == 1
    assert "Una pregunta rápida" in checked[0]
    assert "¿Cuánto tiempo" in checked[0]
    assert len(provider.calls) == 1
    assert result["guardrail"]["provider"] == "Amazon Bedrock Guardrails"
    event = store.get_case("g-rosa").events[-1]
    assert event["detail"]["guardrail"]["action"] == "NONE"


def test_manual_email_control_can_resend_an_existing_question(tmp_path, monkeypatch):
    prepare_rosa(tmp_path, monkeypatch)

    first = send_ask("g-rosa")
    second = send_manual_email("g-rosa")

    assert first["message_id"] == "msg-g-rosa-m-rosa-1"
    assert second["message_id"] == "msg-g-rosa-m-rosa-1-2"
    assert second["attempt"] == 2
    assert second["purpose"] == "question"
    rosa = store.get_case("g-rosa")
    assert sum(event["kind"] == "patient_asked" for event in rosa.events) == 2


def test_manual_email_control_uses_safe_general_message_for_every_patient(tmp_path, monkeypatch):
    prepare_rosa(tmp_path, monkeypatch)
    before = store.get_case("g-linh")

    result = send_manual_email("g-linh")

    assert result["delivery"] == "preview"
    assert result["purpose"] == "general_reminder"
    assert result["to"] == "linh.tran@example.com"
    assert "coverage review" in result["preview"]["body"]
    assert "exempt" not in result["preview"]["body"].lower()
    after = store.get_case("g-linh")
    assert after.status == before.status
    assert after.events[-1]["kind"] == "patient_email_sent"


def test_dummy_recipient_is_previewed_even_when_resend_is_enabled(tmp_path, monkeypatch):
    prepare_rosa(tmp_path, monkeypatch)
    add_dummy_patient_case()
    settings = replace(
        email_settings(),
        backend="resend",
        resend_api_key="re_test_secret",
    )

    result = send_ask("g-dummy", settings=settings)

    assert result["provider"] == "local"
    assert result["delivery"] == "preview"
    assert result["to"] == "dummy.patient@example.com"
