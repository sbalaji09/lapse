from engine import store
from engine.loop.voice import handle_voice_transcript
from engine.models import Source


def test_voice_transcript_reuses_inbound_parser(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "voice.sqlite"))
    store.load_fixtures()
    result = handle_voice_transcript(
        "g-rosa",
        "Dejé de trabajar en marzo; solo puedo estar de pie diez minutos.",
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
