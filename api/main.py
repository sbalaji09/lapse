import json
from datetime import date

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from api.dev import router as dev_router
from engine import store
from engine.config import ACTIVE_RULE_PACK, AS_OF_DATE, DATA_DIR
from engine.rulepack import load_pack
from engine.loop.database import check_database
from engine.loop.clinician import case_for_token, clinician_card, clinician_token, sign_clinician
from engine.loop.inbound import handle_reply
from engine.loop.outbound import send_ask
from engine.loop.pdf import build_appeal_pdf, build_attestation_pdf

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/api")

def not_implemented():
    raise HTTPException(status_code=501, detail="not implemented")


class ReplyBody(BaseModel):
    text: str
    language: str | None = None


class DecisionBody(BaseModel):
    decision: str


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


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/summary")
def get_summary():
    return summary_dict(store.list_cases())


@router.post("/run/state")
def run_state():
    return summary_dict(store.list_cases(), channel_a_only=True)


@router.post("/run/evidence")
def run_evidence():
    return summary_dict(store.list_cases())


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
            "clinician_name": c.clinician_name,
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
    return {**case.model_dump(mode="json"), "notes": [n.model_dump(mode="json") for n in notes],
            "clinician_url": f"/clinician/{clinician_token(id)}"}     # what "Send to Dr. X" opens


@router.post("/cases/{id}/ask")
def case_ask(id: str):
    try:
        return send_ask(id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cases/{id}/reply")
def case_reply(id: str, body: ReplyBody):
    try:
        return handle_reply(id, body.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@router.post("/rulepack/{state}")
def post_rulepack(state: str):
    not_implemented()


@router.post("/demo/reset")
def demo_reset():
    count = store.reset_demo()
    return {"reloaded": count}


app.include_router(router)
app.include_router(dev_router)
