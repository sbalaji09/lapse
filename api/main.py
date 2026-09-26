import asyncio
import contextlib
import json
import logging
from datetime import date
from urllib.parse import parse_qs

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from api.dev import router as dev_router
from engine import guardrails, live_notes, store
from engine.config import ACTIVE_RULE_PACK, AS_OF_DATE, DATA_DIR
from engine.rulepack import load_pack
from engine.loop.database import check_database
from engine.loop.clinician import case_for_token, clinician_card, clinician_token, sign_clinician
from engine.loop.inbound import handle_reply
from engine.loop.outbound import email_health, send_ask, send_manual_email
from engine.loop.pdf import build_appeal_pdf, build_attestation_pdf
from engine.loop.voice import (
    process_due_voice_calls,
    simulate_local_voice_call,
    start_voice_call,
    twilio_status_event,
    verify_twilio_signature,
    voice_case_status,
    voice_health,
    voice_settings,
)

log = logging.getLogger(__name__)


async def voice_scheduler() -> None:
    while True:
        settings = voice_settings()
        try:
            await asyncio.to_thread(process_due_voice_calls, settings=settings)
        except Exception:
            log.exception("voice scheduler iteration failed")
        await asyncio.sleep(settings.scheduler_interval_seconds)


@contextlib.asynccontextmanager
async def lifespan(application: FastAPI):
    task = None
    if voice_settings().automation_enabled:
        task = asyncio.create_task(voice_scheduler())
        application.state.voice_scheduler_task = task
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def _ensure_demo_data() -> None:
    """Never boot to an empty queue: load the golden cases on first start."""
    if not store.list_cases():
        store.load_fixtures()


router = APIRouter(prefix="/api")

def not_implemented():
    raise HTTPException(status_code=501, detail="not implemented")


class ReplyBody(BaseModel):
    text: str
    language: str | None = None


class DecisionBody(BaseModel):
    decision: str


class GuardrailBody(BaseModel):
    text: str


class VoiceSimulationBody(BaseModel):
    outcome: str = "completed"


def summary_dict(cases, channel_a_only: bool = False) -> dict:
    cohort = len(cases)
    # "Cleared" = compliant OR exempt: people who meet the hours/income rule are never contacted either.
    a_exempt = sum(1 for c in cases if c.determination_a.status != "not_determined")
    a_not_determined = sum(1 for c in cases if c.determination_a.status == "not_determined")
    result = {
        "cohort": cohort,
        "a_exempt": a_exempt,
        "a_not_determined": a_not_determined,
        "rule_pack": load_pack(ACTIVE_RULE_PACK).version,
    }
    if channel_a_only:
        return result
    result.update({
        "final_exempt": sum(1 for c in cases if c.determination_final.status != "not_determined"),
        "provable": sum(1 for c in cases if c.bucket.value == "PROVABLE"),
        "one_away": sum(1 for c in cases if c.bucket.value == "ONE_AWAY"),
        "no_path": sum(1 for c in cases if c.bucket.value == "NO_PATH"),
        "fragile": sum(1 for c in cases if c.fragile),
        # People the state would have dropped whom Lapse has cleared (chart, databases, replies, signatures).
        "recovered": sum(1 for c in cases if c.determination_a.status == "not_determined"
                         and c.determination_final.status != "not_determined"),
        "verifier_dropped": sum(len(c.dropped_claims) for c in cases),
    })
    return result


def email_outreach_state(case) -> dict:
    event = next(
        (
            event
            for event in reversed(case.events)
            if event.get("kind") in {"patient_asked", "patient_email_sent"}
        ),
        None,
    )
    if event:
        detail = event.get("detail") or {}
        delivery = detail.get("delivery", "preview")
        return {
            "status": "sent" if delivery == "sent" else "preview",
            "sent": delivery == "sent",
            "at": event.get("at"),
            "to": detail.get("to"),
        }

    has_email = bool(case.email)
    return {
        "status": "not_sent" if has_email else "not_applicable",
        "sent": False,
        "at": None,
        "to": case.email if has_email else None,
    }


@router.get("/health")
def health():
    return {"status": "ok", "email": email_health(), "voice": voice_health()}


@router.get("/voice/health")
def get_voice_health():
    return voice_health()


@router.get("/email/health")
def get_email_health():
    return email_health()


@router.get("/summary")
def get_summary():
    return summary_dict(store.list_cases())


@router.post("/run/state")
def run_state():
    return summary_dict(store.list_cases(), channel_a_only=True)


@router.post("/run/evidence")
def run_evidence():
    try:
        return live_notes.start()
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get("/run/evidence/status")
def evidence_status():
    return live_notes.status()


@router.get("/run/evidence/stream")
def evidence_stream(run_id: str):
    return StreamingResponse(
        live_notes.event_stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/queue")
def get_queue(bucket: str | None = None, window_days: int | None = None):
    cases = store.list_cases(bucket=bucket, window_days=window_days)
    out = []
    for c in cases:
        top_missing = next((m for m in c.missing if m.status in ("open", "asked")), None)   # resolved steps are history, not work
        out.append({
            "id": c.patient_id,
            "name": c.display_name,
            "age": c.age,
            "language": c.language,
            "renewal_date": c.renewal_date.isoformat(),
            "days_to_renewal": (c.renewal_date - AS_OF_DATE).days,
            "bucket": c.bucket.value,
            "fragile": c.fragile,
            "status": c.status.value,
            "state_status": c.determination_a.status,
            "state_rule_ids": c.determination_a.rule_ids,
            "verified_spans": len(c.claims),
            "clinician_name": c.clinician_name,
            "email_outreach": email_outreach_state(c),
            "top_missing_fact": {
                "key": top_missing.key,
                "holder": top_missing.holder.value,
                "database": top_missing.database,
                "why": top_missing.why,
            } if top_missing else None,
        })
    return out


@router.get("/cases/{id}")
def get_case_detail(id: str):
    case = store.get_case(id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    notes = store.get_notes(id)
    return {
        **case.model_dump(mode="json"),
        "notes": [n.model_dump(mode="json") for n in notes],
        "clinician_url": f"/clinician/{clinician_token(id)}",
        "email_outreach": email_outreach_state(case),
        "voice": voice_case_status(id),
    }     # what "Send to Dr. X" opens


@router.post("/cases/{id}/ask")
def case_ask(id: str):
    try:
        return send_ask(id)
    except guardrails.GuardrailIntervened:
        raise HTTPException(
            status_code=422,
            detail="Email blocked because it included privileged eligibility or case-status information.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/cases/{id}/email")
def case_email(id: str):
    try:
        return send_manual_email(id)
    except guardrails.GuardrailIntervened:
        raise HTTPException(
            status_code=422,
            detail="Email blocked because it included privileged eligibility or case-status information.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/cases/{id}/reply")
def case_reply(id: str, body: ReplyBody):
    try:
        return handle_reply(id, body.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/cases/{id}/voice")
def case_voice_status(id: str):
    try:
        return voice_case_status(id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/cases/{id}/voice/call")
def case_voice_call(id: str):
    try:
        return start_voice_call(id, force=True).model_dump(mode="json")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/cases/{id}/voice/simulate")
def case_voice_simulate(id: str, body: VoiceSimulationBody):
    try:
        return simulate_local_voice_call(id, outcome=body.outcome)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/voice/twilio/status/{session_id}")
async def receive_twilio_status(
    session_id: str,
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
):
    settings = voice_settings()
    if not settings.public_api_url:
        raise HTTPException(status_code=503, detail="LAPSE_PUBLIC_API_URL is not configured")
    callback_url = f"{settings.public_api_url}{request.url.path}"
    if request.url.query:
        callback_url += f"?{request.url.query}"
    raw = (await request.body()).decode()
    params = parse_qs(raw, keep_blank_values=True)
    if not verify_twilio_signature(
        callback_url,
        params,
        x_twilio_signature,
        settings.twilio_auth_token,
    ):
        raise HTTPException(status_code=401, detail="invalid Twilio signature")
    provider_status = params.get("CallStatus", [""])[0]
    if not provider_status:
        raise HTTPException(status_code=400, detail="CallStatus is required")
    try:
        twilio_status_event(
            session_id,
            provider_status,
            call_sid=params.get("CallSid", [None])[0],
            error_code=params.get("ErrorCode", [None])[0],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


@router.post("/inbound/email")
def inbound_email(body: dict):
    try:
        return handle_reply(body["case_id"], body["text"], body.get("message_id"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cases/{id}/check-database")
def case_check_database(id: str):
    try:
        return check_database(id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/clinician/{token}")
def get_clinician(token: str):
    case = case_for_token(token)
    if case is None:
        raise HTTPException(status_code=404, detail="invalid token")
    return clinician_card(case)


@router.post("/clinician/{token}")
def post_clinician(token: str, body: DecisionBody):
    case = case_for_token(token)
    if case is None:
        raise HTTPException(status_code=404, detail="invalid token")
    try:
        return sign_clinician(case.patient_id, body.decision)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/cases/{id}/attestation.pdf")
def case_attestation_pdf(id: str):
    case = store.get_case(id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    rule_id = case.determination_final.rule_ids[0] if case.determination_final.rule_ids else "unknown"
    pdf_bytes = build_attestation_pdf(case, rule_id, case.clinician_name)
    return Response(content=pdf_bytes, media_type="application/pdf")


@router.get("/cases/{id}/appeal.pdf")
def case_appeal_pdf(id: str, termination_date: date):
    case = store.get_case(id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return Response(
        content=build_appeal_pdf(case, termination_date, case.clinician_name),
        media_type="application/pdf",
    )


@router.get("/eval")
def get_eval():
    # Written by `python -m engine.eval` (Track A); measured against labels we wrote on synthetic data.
    path = DATA_DIR / "eval.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no eval yet; run `make eval`")
    return json.loads(path.read_text())


@router.get("/fragile")
def get_fragile():
    return [c.model_dump(mode="json") for c in store.list_cases(fragile=True)]


@router.post("/guardrails/check")
def check_guardrail(body: GuardrailBody):
    try:
        return guardrails.check_patient_message(body.text)
    except guardrails.GuardrailNotConfigured as error:
        raise HTTPException(status_code=503, detail=str(error))
    except guardrails.GuardrailCacheMiss as error:
        raise HTTPException(status_code=503, detail=str(error))


@router.post("/rulepack/{state}")
def post_rulepack(state: str):
    not_implemented()


@router.post("/demo/reset")
def demo_reset():
    count = store.reset_demo()
    return {"reloaded": count}


app.include_router(router)
app.include_router(dev_router)
