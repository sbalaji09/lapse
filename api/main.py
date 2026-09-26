from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"status": "ok"}


def not_implemented():
    raise HTTPException(status_code=501, detail="not implemented")


@router.get("/summary")
def get_summary():
    not_implemented()


@router.post("/run/state")
def run_state():
    not_implemented()


@router.post("/run/evidence")
def run_evidence():
    not_implemented()


@router.get("/queue")
def get_queue(bucket: str = None, window_days: int = None):
    not_implemented()


@router.get("/cases/{id}")
def get_case(id: str):
    not_implemented()


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
    not_implemented()


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
    not_implemented()


@router.post("/rulepack/{state}")
def post_rulepack(state: str):
    not_implemented()


@router.post("/demo/reset")
def demo_reset():
    not_implemented()


app.include_router(router)
