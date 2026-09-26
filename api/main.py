import hashlib
import hmac
from datetime import date

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from engine import store
from engine.config import ACTIVE_RULE_PACK, AS_OF_DATE

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/api")

CLINICIAN_TOKEN_SECRET = b"lapse-demo-secret"  # hackathon demo only, not real auth


def not_implemented():
    raise HTTPException(status_code=501, detail="not implemented")


def clinician_token(case_id: str) -> str:
    return hmac.new(CLINICIAN_TOKEN_SECRET, case_id.encode(), hashlib.sha256).hexdigest()[:16]


def case_for_token(token: str):
    for case in store.list_cases():
        if clinician_token(case.patient_id) == token:
            return case
    return None


def summary_dict(cases, channel_a_only: bool = False) -> dict:
    cohort = len(cases)
    a_exempt = sum(1 for c in cases if c.determination_a.status == "exempt")
    a_not_determined = sum(1 for c in cases if c.determination_a.status == "not_determined")
    result = {
        "cohort": cohort,
        "a_exempt": a_exempt,
        "a_not_determined": a_not_determined,
        "rule_pack": ACTIVE_RULE_PACK,
    }
    if channel_a_only:
        return result
    result.update({
        "final_exempt": sum(1 for c in cases if c.determination_final.status == "exempt"),
        "provable": sum(1 for c in cases if c.bucket.value == "PROVABLE"),
        "one_away": sum(1 for c in cases if c.bucket.value == "ONE_AWAY"),
        "no_path": sum(1 for c in cases if c.bucket.value == "NO_PATH"),
        "fragile": sum(1 for c in cases if c.fragile),
        "recovered": sum(1 for c in cases for m in c.missing if m.status == "resolved_true"),
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
        top_missing = c.missing[0] if c.missing else None
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
            "top_missing_fact": {
                "key": top_missing.key,
                "holder": top_missing.holder.value,
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
    return {**case.model_dump(mode="json"), "notes": [n.model_dump(mode="json") for n in notes]}


@router.post("/cases/{id}/ask")
def case_ask(id: str):
    not_implemented()


@router.post("/cases/{id}/reply")
def case_reply(id: str):
    not_implemented()


@router.post("/inbound/email")
def inbound_email():
    not_implemented()


@router.post("/cases/{id}/check-database")
def case_check_database(id: str):
    not_implemented()


@router.get("/clinician/{token}")
def get_clinician(token: str):
    case = case_for_token(token)
    if case is None:
        raise HTTPException(status_code=404, detail="invalid token")
    return {"patient_id": case.patient_id, "name": case.display_name, "status": case.status.value}


@router.post("/clinician/{token}")
def post_clinician(token: str):
    not_implemented()


@router.get("/cases/{id}/attestation.pdf")
def case_attestation_pdf(id: str):
    not_implemented()


@router.get("/eval")
def get_eval():
    not_implemented()


@router.get("/fragile")
def get_fragile():
    return [c.model_dump(mode="json") for c in store.list_cases(fragile=True)]


@router.post("/rulepack/{state}")
def post_rulepack(state: str):
    not_implemented()


@router.post("/demo/reset")
def demo_reset():
    count = store.load_fixtures()
    return {"reloaded": count}


app.include_router(router)
