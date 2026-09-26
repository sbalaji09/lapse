# PROJECT - read this first, every agent

## Context


### The product in one line
When a state checks whether a Medicaid adult is exempt from the new work requirement, it reads billing codes. We find everyone that check will wrongly drop, work out the one fact that would save each of them, and go get it - from the patient, the doctor, or a database that already knows.

### The rule (use these facts, they correct earlier drafts)
Federal law (Pub. L. 119-21, §71119 - verify cite before putting it on screen) requires expansion adults aged 19-64 to show "community engagement" to keep Medicaid. Starts January 1, 2027. Checked at application and at every renewal, not in one January sweep. California moves these adults to renewals every 6 months, so renewals are rolling: at any moment some people are inside their 30-day window. **The queue is a permanent, live thing, not a seasonal report.**

#### A person is fine if ANY of these is true
Compliance (they meet it):
- 80+ hours/month of work, OR half-time school, OR community service/volunteering, OR a work program, OR any mix totaling 80
- OR monthly income >= $580 (80 x federal minimum wage). **Check income first** - these people are safe and must never be contacted.

Exemptions (they're excused):
- Medically frail - two SEPARATE tests, never collapsed: (1) has a qualifying condition category (blind, disabled, SUD, disabling mental disorder, physical/intellectual/developmental disability, serious or complex medical condition) AND (2) it significantly impairs ability to work / perform daily activities. Test (2) is under litigation; keeping it a separate boolean means we survive either ruling.
- Parent or caretaker of a child 13 or under, or of a disabled person
- Pregnant or postpartum
- American Indian / Alaska Native
- Veteran with a total disability rating
- Already meeting SNAP or TANF work requirements
- In drug or alcohol (SUD) treatment
- Recently released from incarceration (window is state config)
- Former foster youth (under 26)
- Short-term hardship: hospitalization, or living in a county with high unemployment or a declared disaster (county, not zip code)

#### How the state checks (what Channel A simulates)
Ex parte: the state looks at data it already has, 12-month lookback. For medical frailty, Nebraska (first state live, May 2026) reads **only the primary diagnosis** on each claim against a code list. So a patient whose last visit was billed as a cough doesn't count, even if diabetes with neuropathy is why they can't work. That's not a Nebraska quirk to improve on - it's the flaw we exploit.

If the state can't verify, it must send a notice and give the person **30 days** to respond. Nebraska lets people **self-declare** medical frailty; the federal rule lets enrollees self-attest from 2027 (state-by-state). So the patient's own answer legally counts - the patient is a first-class source of facts.

#### Correct vocabulary (judges will catch these)
- We produce a **medical exemption attestation**, not a "renewal application". The renewal form is a separate state document.
- Clinics see **their own patients**, not "the state's patients". We are not selling to the state.
- California numbers are three separate projections, never merge them: ~4.8M subject to the requirement, ~2.2M expected to qualify for an exemption, ~1.1M expected to lose coverage by 2029-30. The 1.1M is total coverage loss, not "people denied an exemption".

### The architecture
1. **Channel A - State Simulator.** Deterministic, zero LLM. Rule pack compiled to code. Primary dx only, 12-month lookback, state code list, plus the other exemptions the state can see in its own data. This is the honest baseline.
2. **Channel B - Evidence Finder.** LLM over clinical notes. Every claim carries the exact quote and character offsets. A **verifier** sees only (claim, quoted span) and must agree; unsupported claims are dropped and counted.
3. **Buckets.**
   - SAFE - compliant or Channel A exempts. Do nothing. Say out loud that doing nothing is the cheapest action.
   - PROVABLE - Channel A doesn't exempt, Channel B's verified evidence does. Route to clinician to sign.
   - ONE_AWAY - exactly one missing fact would flip them. This is the product.
   - NO_PATH - no exemption within one fact; they need hours-reporting help (out of scope, just shown).
   - FRAGILE (flag on SAFE) - Channel A exempts on medical frailty but the chart has nothing supporting it. Audit exposure. Sold to the plan.
4. **Counterfactual solver.** Deterministic search over the rule tree: which single unknown fact, if true, flips the determination, and **who holds that fact**. Database first (bother nobody), then clinician, then patient. We never ask a person for something we could look up.
5. **Close the loop.** Email the patient one question in their language -> they reply in their own words -> parse into a typed fact, raw reply stored as evidence -> re-run -> case flips -> clinician card -> sign -> attestation PDF with provenance for every fact. Voice call for non-responders (stretch).

### Users
| Who | Surface | Never |
|---|---|---|
| Clinic enrollment worker (daily operator) | Full web queue sorted by renewal date | - |
| Patient | One email in their language, one question, reply in plain words. Voice call if no reply. | Login, form, dashboard, legal/medical language, any statement about eligibility |
| Clinician | One card: one question, 3 highlighted sentences, Sign / Decline | Dashboard, queue, writing anything |
| Health plan (MCO) or clinic - the buyer | Aggregate view: at-risk, recovered, fragile list | - |

No patient dashboard. People at risk of procedural disenrollment are exactly the people who don't log into portals.

### Refusals (hard rules, enforce in code and copy)
- Never tell a patient whether they are eligible.
- Never state a clinical conclusion; the clinician attests.
- Never show a claim without a clickable source span.
- Never ask a patient something we could answer from data we have.
- Synthetic data only. Say "synthetic" in the first 15 seconds of the demo.
- Not political. Framing: "keep eligible people covered" - the state's own stated goal.

### Stack
Python 3.11 + pydantic (engines), SQLite (store), FastAPI (api), Next.js App Router + TypeScript (web), OpenAI models (fast model for extraction/parsing, set in `engine/config.py`; Bedrock is a config swap), Resend for outbound email, reportlab for PDF.
Not doing: Lambda/Dynamo/S3/EventBridge, Twilio SMS, real state form templates, auth, mobile.

---

## Contracts - the seams between the two tracks

If you need to change anything in this file, stop and ask your human. Both tracks depend on it.

### Repo layout
```
lapse/
  engine/                  # Python package: lapse engines (Track A)
    config.py              # AS_OF_DATE, model names, paths, active rule pack
    models.py              # SHARED pydantic models (below)
    llm.py                 # OpenAI wrapper + disk cache (Track A writes, B reuses)
    cohort.py              # load Synthea FHIR -> Patient
    notes.py               # note generator + labels
    channel_a.py           # state simulator
    channel_b.py           # evidence finder
    verifier.py
    buckets.py
    solver.py
    eval.py
    pipeline.py            # run everything -> store
    store.py               # SQLite read/write of models
    loop/                  # Track B: reply parsing, re-determination, pdf, email, voice
  rules/                   # SHARED: ca.yaml, ne.yaml, fact_registry.yaml, codes/
  data/
    synthea/               # raw FHIR (gitignored)
    truth/                 # ground truth per patient (A1)
    external/              # mock databases: snap.json, school.json, release.json, county.json, va.json
  fixtures/                # SHARED: golden_cases.json (7 hero patients, fully populated)
  api/main.py              # FastAPI (Track B)
  web/                     # Next.js (Track B)
  demo/                    # demo reset script, recorded video, voice clip
```

### Time
`AS_OF_DATE = 2027-02-15` (in `engine/config.py`). The requirement is live; renewals are rolling. Rosa's renewal is 2027-03-04. All "renewal in N days" math uses AS_OF_DATE, never the wall clock.

### Shared models (`engine/models.py`)
```python
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
```

### Fact registry (`rules/fact_registry.yaml`)
Every fact key the rules or the solver can mention. Holder order is preference order; the solver picks the first holder that is available for that patient.
```yaml
standing_tolerance_minutes: {type: int, holders: [patient, clinician], impairs_if: "< 30",
  question: {en: "How long can you stand before you need to sit down?", es: "¿Cuánto tiempo puede estar de pie antes de necesitar sentarse?"}}
limitation_attested:        {type: bool, holders: [clinician],
  question: {en: "Does the record support that {condition} limits {name}'s ability to work?"}}
monthly_income:             {type: int, holders: [database, patient], database: state_wage_records}
hours_per_month:            {type: int, holders: [patient]}
enrolled_half_time_school:  {type: bool, holders: [database, patient], database: student_enrollment}
snap_tanf_work_compliant:   {type: bool, holders: [database], database: snap}
in_sud_treatment:           {type: bool, holders: [clinician, patient]}
released_incarceration_days:{type: int, holders: [database], database: corrections}
dependent_child_13_or_under:{type: bool, holders: [patient]}
caregiver_disabled_person:  {type: bool, holders: [patient]}
pregnant_or_postpartum:     {type: bool, holders: [clinician, patient]}
ai_an:                      {type: bool, holders: [patient]}
veteran_total_disability:   {type: bool, holders: [database], database: va}
former_foster_youth:        {type: bool, holders: [database, patient], database: child_welfare}
county_hardship:            {type: bool, holders: [database], database: county}
```
Track A owns the file content; keys above are the agreed minimum. Add keys, never rename.

### Rule pack (`rules/ca.yaml`) - one per state
```yaml
state: CA
version: ca-2027.02-demo
program: "Medi-Cal expansion adults 19-64"
citation: "Pub. L. 119-21 §71119 (verify)"
lookback_months: 12
renewal_cadence_months: 6
notice_response_days: 30
self_attestation_allowed: {medically_frail: true}
compliance:
  - {id: hours,  requires: ["hours_per_month >= 80"]}
  - {id: income, requires: ["monthly_income >= 580"]}
  - {id: school, requires: ["enrolled_half_time_school"]}
exemptions:
  - id: medically_frail
    state_ex_parte: {method: primary_dx_only, code_list: codes/ca_frailty.txt}
    requires: [qualifying_condition, significantly_impairs]
  - {id: parent_caretaker, requires: ["dependent_child_13_or_under | caregiver_disabled_person"]}
  - {id: pregnant_postpartum, requires: [pregnant_or_postpartum]}
  - {id: ai_an, requires: [ai_an]}
  - {id: veteran_disability, requires: [veteran_total_disability]}
  - {id: snap_tanf, requires: [snap_tanf_work_compliant]}
  - {id: sud_treatment, requires: [in_sud_treatment]}
  - {id: recent_release, requires: ["released_incarceration_days <= 90"]}
  - {id: foster_youth, requires: [former_foster_youth]}
  - {id: hardship_county, requires: [county_hardship]}
state_visible_sources: [billing_code, structured_record, snap, state_wage_records, corrections]
```
`state_visible_sources` defines what Channel A may read. Anything else (notes, student enrollment, VA, patient replies) is invisible to the state simulator. Swapping states = swapping this file.

### API (FastAPI, `api/main.py`, all JSON, prefix `/api`)
| Method | Path | Returns |
|---|---|---|
| GET | `/summary` | `{cohort, a_exempt, a_not_determined, final_exempt, provable, one_away, no_path, fragile, recovered, verifier_dropped, rule_pack}` |
| POST | `/run/state` | same counts, Channel A only (replays cached run; for the demo reveal) |
| POST | `/run/evidence` | counts after Channel B |
| GET | `/queue?bucket=&window_days=` | `Case[]` light: id, name, age, language, renewal_date, bucket, fragile, status, top missing fact |
| GET | `/cases/{id}` | full `Case` + `notes: Note[]` |
| POST | `/cases/{id}/ask` | sends question for top open MissingFact; `{channel: "email"|"voice", message_id}` |
| POST | `/cases/{id}/reply` | body `{text, language?}`; simulated inbound, same code path as webhook |
| POST | `/inbound/email` | Resend/inbound webhook -> same handler as `/reply` |
| POST | `/cases/{id}/check-database` | resolves a database-held MissingFact automatically |
| GET | `/clinician/{token}` | card: `{name, question, condition, spans:[{note_id,quote,start,end,date}]}` |
| POST | `/clinician/{token}` | `{decision: "sign"|"decline"}` |
| GET | `/cases/{id}/attestation.pdf` | PDF |
| GET | `/eval` | `{patient_level:{a:{p,r,f1}, final:{p,r,f1}, confusion}, claim_level:{p,r,f1}, verifier:{kept,dropped}}` |
| GET | `/fragile` | `Case[]` light |
| POST | `/rulepack/{state}` | switch active pack, re-run A + buckets (stretch) |
| POST | `/demo/reset` | restores golden cases to pre-demo state |

### Golden fixtures (`fixtures/golden_cases.json`)
Seven hand-built patients, full `Case` objects plus notes. A1 must force these same seven into the real cohort with the same ids so the demo works on either.
| id | Name | Story | Bucket | Top missing fact / holder |
|---|---|---|---|---|
| g-rosa | Rosa Delgado, 52, es | Only billed primary dx in 12 mo: acute bronchitis. Notes: type 2 diabetes with peripheral neuropathy (qualifying = true) but nothing on function (impairs = unknown). Truth: stands ~10 min. | ONE_AWAY | standing_tolerance_minutes / patient |
| g-marcus | Marcus Webb, 58, en | Primary dx last year: ankle sprain. Notes: severe COPD, "cannot walk more than half a block without stopping". | PROVABLE | limitation_attested / clinician |
| g-deshawn | Deshawn Price, 23, en | No hours on file. Enrolled half-time at community college (student DB). | ONE_AWAY | enrolled_half_time_school / database |
| g-linh | Linh Tran, 41, vi | Primary dx on code list (CKD stage 4), notes support it. | SAFE | - |
| g-karen | Karen Hollis, 47, en | State exempt via primary dx "major depressive disorder" on list. Notes: "in remission, working full time". | SAFE, fragile | - |
| g-omar | Omar Haddad, 35, en | Wage records show $920/month. | SAFE (compliant) | - never contacted |
| g-bea | Bea Knox, 60, en | Notes contain "patient's mother has severe arthritis" (bait - verifier must drop). No path. | NO_PATH | - |
