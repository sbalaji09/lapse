import pytest


@pytest.fixture(autouse=True)
def disable_live_communications(monkeypatch):
    """Tests must never send real email or place real phone calls from a developer's .env."""
    from engine import guardrails

    monkeypatch.setenv("EMAIL_BACKEND", "local")
    monkeypatch.setenv("VOICE_BACKEND", "local")
    monkeypatch.setenv("DEMO_INBOX", "siddharthbalaji6@gmail.com")
    monkeypatch.setenv("VOICE_DESTINATION_PHONE", "+14086100377")
    monkeypatch.setattr(guardrails, "configured", lambda: False)
