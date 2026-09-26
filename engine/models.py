"""Shared pydantic models. This is a contract with Track B (PROJECT.md); change it only by agreement."""
from enum import Enum
from datetime import date, datetime
from pydantic import BaseModel


class Source(str, Enum):
    billing_code = "billing_code"          # claim primary/secondary dx
    structured_record = "structured_record"  # FHIR demographics etc.
    note_span = "note_span"
    patient_reply = "patient_reply"
    clinician_attestation = "clinician_attestation"
    external_db = "external_db"


class Holder(str, Enum):
    database = "database"
    clinician = "clinician"
    patient = "patient"


class Tri(str, Enum):            # three-valued logic, never collapse unknown into false
    true = "true"; false = "false"; unknown = "unknown"


class Fact(BaseModel):
    id: str
    patient_id: str
    key: str                      # must exist in rules/fact_registry.yaml
    value: bool | int | float | str | None
    source: Source
    source_ref: dict              # note_span: {note_id,start,end}; billing_code: {claim_id,code,sequence};
                                  # patient_reply: {message_id}; external_db: {db,record_id}; clinician: {attestation_id}
    quote: str | None = None      # exact text when source is note_span or patient_reply
    recorded_at: datetime
    rule_pack_version: str


class Note(BaseModel):
    id: str; patient_id: str; date: date; author: str; text: str


class Claim(BaseModel):          # Channel B output (an evidence claim, not an insurance claim)
    id: str; patient_id: str; note_id: str
    category: str                 # e.g. "physical_disability", "serious_mental_illness", "sud"
    condition: str                # plain name, e.g. "diabetic peripheral neuropathy"
    qualifying_category: bool
    significantly_impairs: Tri
    quote: str; start: int; end: int   # text[start:end] == quote, ALWAYS, checked in code
    verified: bool | None = None       # None = not yet verified
    verifier_reason: str | None = None


class Determination(BaseModel):
    patient_id: str
    channel: str                  # "A" | "B" | "final"
    status: str                   # "compliant" | "exempt" | "not_determined"
    rule_ids: list[str]           # which rule(s) satisfied
    fact_ids: list[str]
    rule_pack_version: str


class MissingFact(BaseModel):
    id: str; patient_id: str
    key: str                      # fact_registry key
    holder: Holder
    database: str | None = None   # when holder == database
    unlocks_rule: str             # rule id that flips if this fact is true
    why: str                      # one plain sentence for the operator
    question: dict[str, str]      # language code -> question text (patient/clinician)
    status: str = "open"          # open | asked | answered | resolved_true | resolved_false


class Bucket(str, Enum):
    SAFE = "SAFE"; PROVABLE = "PROVABLE"; ONE_AWAY = "ONE_AWAY"; NO_PATH = "NO_PATH"


class CaseStatus(str, Enum):
    needs_action = "needs_action"; waiting_patient = "waiting_patient"
    waiting_clinician = "waiting_clinician"; attestation_ready = "attestation_ready"; no_action = "no_action"


class Case(BaseModel):
    patient_id: str
    display_name: str; age: int; language: str   # "en" | "es" | ...
    email: str; phone: str | None
    clinic_id: str; clinician_name: str
    renewal_date: date
    bucket: Bucket
    fragile: bool
    status: CaseStatus
    determination_a: Determination
    determination_final: Determination
    claims: list[Claim]
    dropped_claims: list[Claim]          # verifier rejected, kept for the drop count
    facts: list[Fact]
    missing: list[MissingFact]           # solver output, best first
    billed_dx_12mo: list[dict]           # [{date, code, display, sequence}] - what the state sees
    events: list[dict]                   # audit log: [{at, kind, detail}]


class VoiceSessionStatus(str, Enum):
    dialing = "dialing"
    connected = "connected"
    completed = "completed"
    no_answer = "no_answer"
    declined = "declined"
    cancelled = "cancelled"
    needs_human = "needs_human"
    failed = "failed"


class VoiceSession(BaseModel):
    id: str
    case_id: str
    missing_fact_id: str
    provider: str
    provider_contact_id: str | None = None
    status: VoiceSessionStatus
    attempt: int
    phone: str
    language: str
    message: str = ""
    created_at: datetime
    updated_at: datetime
    due_at: datetime
    transcript: str | None = None
    recording_ref: str | None = None
    result: dict | None = None
    error: str | None = None
