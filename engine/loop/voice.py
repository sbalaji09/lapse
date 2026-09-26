"""Voice transcript adapter for the inbound patient-reply parser."""

from engine.loop.inbound import handle_reply


def handle_voice_transcript(
    case_id: str,
    transcript: str,
    recording_ref: str | None = None,
    message_id: str | None = None,
) -> dict:
    ref = {"channel": "voice"}
    if recording_ref:
        ref["recording_ref"] = recording_ref
    return handle_reply(
        case_id,
        transcript,
        message_id or f"voice-{case_id}",
        source_ref=ref,
    )
