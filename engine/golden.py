"""Builds fixtures/golden_cases.json: the 7 hand-built demo patients (PROJECT.md, golden fixtures table).

Notes are written by hand here. Claim and fact offsets are never typed: they are computed with
text.find(quote), and the build fails if a quote is missing or ambiguous.

    python -m engine.golden        # rewrite fixtures/golden_cases.json
"""
import json
from datetime import date, datetime

from engine.config import AS_OF_DATE, FIXTURES_PATH
from engine.models import (Bucket, Case, CaseStatus, Claim, Determination, Fact, Holder, MissingFact, Note,
                           Source, Tri)

PACK = "ca-2027.02-demo"
RUN_AT = datetime(AS_OF_DATE.year, AS_OF_DATE.month, AS_OF_DATE.day, 6, 0, 0)

# Registry questions, filled per patient (see rules/fact_registry.yaml).
Q_STANDING = {"en": "How long can you stand before you need to sit down?",
              "es": "¿Cuánto tiempo puede estar de pie antes de necesitar sentarse?"}


def q_limitation(condition: str, name: str) -> dict[str, str]:
    return {"en": f"Does the record support that {condition} limits {name}'s ability to work?"}


# ---------------------------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------------------------

NOTES: dict[str, list[tuple[str, date, str, str]]] = {
    "g-rosa": [
        ("n-rosa-1", date(2026, 4, 8), "Anita Patel, MD", """\
S: 52-year-old woman presents with 9 days of productive cough, low-grade fevers, and chest tightness. \
Spanish-speaking; visit conducted with an in-person interpreter. No known sick contacts. Never smoker. \
History of type 2 diabetes on metformin.
O: T 37.9 C, HR 92, BP 134/82, SpO2 96% on room air. Scattered rhonchi bilaterally, no focal \
consolidation. No accessory muscle use. Pharynx mildly injected.
A: Acute bronchitis, likely viral. No signs of pneumonia on exam. Type 2 diabetes, last A1c above goal.
P: Supportive care with fluids and honey. Albuterol inhaler as needed for chest tightness. Return \
precautions reviewed: fever above 39 C, shortness of breath, or cough beyond 3 more weeks. Recheck A1c \
at next visit. Interpreter used for the entire visit."""),
        ("n-rosa-2", date(2026, 8, 19), "Anita Patel, MD", """\
S: Follow-up for lingering cough after bronchitis in the spring, now mostly resolved. Also reviewing \
diabetes. Reports numbness and burning in both feet, worse at night, present for over a year. Taking \
metformin 1000 mg twice daily; admits missing evening doses about half the time.
O: BP 138/84, BMI 31. Lungs clear. Monofilament sensation absent at 6 of 10 sites bilaterally. Vibration \
sense decreased at both great toes. Pedal pulses 2+. No foot ulcers or calluses.
A: Type 2 diabetes mellitus with diabetic peripheral neuropathy, A1c 8.4%. Resolving post-bronchitic cough.
P: Start gabapentin 300 mg at bedtime, titrate as tolerated. Discussed metformin adherence and a weekly \
pill organizer. Podiatry referral for diabetic foot care. Daily foot checks reviewed with patient via \
interpreter. Return in 3 months."""),
        ("n-rosa-3", date(2026, 12, 2), "Anita Patel, MD", """\
S: Returns with 5 days of dry cough and sore throat after a family member had a cold. No fever at home. \
Diabetes follow-up deferred to next visit at patient request. Gabapentin helping with night-time foot \
pain, though her feet still feel numb most of the day.
O: T 37.2 C, SpO2 98% on room air. Mild pharyngeal erythema, no exudate. Lungs clear to auscultation. \
Feet: skin intact, no ulcers, dry skin at both heels.
A: Cough, likely post-viral upper respiratory infection. Diabetic peripheral neuropathy on gabapentin, \
pain improved.
P: Supportive care. Continue gabapentin 300 mg nightly. Emollient for heels. Keep podiatry appointment \
in January. Return in 6 weeks for diabetes visit with A1c. Interpreter used for the entire visit."""),
    ],
    "g-marcus": [
        ("n-marcus-1", date(2026, 6, 11), "James Okafor, MD", """\
S: 58-year-old man twisted his right ankle stepping off a curb two days ago. Able to bear weight with \
pain. History of COPD on tiotropium and albuterol; former smoker, 40 pack-years, quit in 2021.
O: Right lateral ankle swelling and ecchymosis, tender over the anterior talofibular ligament. No bony \
tenderness at the malleoli or base of the fifth metatarsal; Ottawa rules negative. Lungs with prolonged \
expiration and scattered wheeze. SpO2 93% on room air.
A: Right ankle sprain, grade 1 to 2. COPD, chronic, reported by patient as at baseline.
P: Rest, ice, compression, elevation. Lace-up ankle brace. Ibuprofen as needed with food. No imaging \
needed. Continue inhalers and reviewed inhaler technique. Overdue for spirometry; schedule combined ankle \
recheck and COPD visit in 10 weeks."""),
        ("n-marcus-2", date(2026, 8, 27), "James Okafor, MD", """\
S: Ankle recheck and COPD follow-up. Ankle pain resolved, brace discontinued. Reports shortness of breath \
with minimal exertion such as dressing or showering. Cannot walk more than half a block without stopping \
to rest. Two exacerbations in the past year treated with prednisone. Using albuterol 4 to 5 times daily.
O: Right ankle nontender, full range of motion. SpO2 91% at rest, 86% after walking 40 feet in the \
hallway. Diffuse expiratory wheeze. Spirometry: FEV1 42% predicted, FEV1/FVC 0.52.
A: Severe COPD, GOLD stage 3, with frequent exacerbations and exertional hypoxemia. Ankle sprain, resolved.
P: Add inhaled corticosteroid to current LAMA/LABA. Refer for ambulatory oxygen evaluation and pulmonary \
rehabilitation. Pneumococcal vaccine given today. Written exacerbation action plan provided and \
reviewed. Return in 6 weeks."""),
        ("n-marcus-3", date(2026, 11, 19), "Dana Whitfield, RN", """\
Telephone encounter. Patient called to report that his oxygen concentrator was delivered and he is using \
it with exertion and overnight. Asked about the pulmonary rehab schedule; first session confirmed for \
December 3. He states getting to the bus stop leaves him too winded, so his daughter will drive him to \
sessions. Denies fever, chest pain, or change in sputum color. Using albuterol about 3 times daily, fewer \
than at his last visit. Reviewed the exacerbation action plan: start the prednisone pack on hand and call \
the clinic if sputum turns green or breathing worsens over 24 hours. Reminded him to bring all inhalers \
to the first rehab session. Patient verbalized understanding and repeated the plan back. Routed to \
Dr. Okafor for review."""),
    ],
    "g-deshawn": [
        ("n-deshawn-1", date(2026, 5, 2), "Maria Reyes, MD", """\
S: 23-year-old man injured his left wrist yesterday falling on an outstretched hand during pickup \
basketball. Pain on the thumb side, worse with gripping. No numbness or tingling. Right-hand dominant. \
No prior wrist injuries. No medications, no allergies.
O: Mild swelling dorsal left wrist. No snuffbox tenderness. Full range of motion with pain at extremes of \
extension. Grip strength slightly reduced on the left. Radial pulse 2+, sensation intact.
A: Left wrist sprain. Low suspicion for scaphoid fracture given exam; no imaging today.
P: Wrist splint for comfort for 1 week. Ice and ibuprofen as needed. Return if snuffbox tenderness \
develops or pain is not improving in 10 days, in which case obtain wrist x-rays. Discussed warm-up and \
wrist taping before games."""),
        ("n-deshawn-2", date(2026, 9, 14), "Maria Reyes, MD", """\
S: Five days of nasal congestion, clear rhinorrhea, facial pressure, and mild headache. No fever. \
Roommate had similar symptoms last week. Wrist fully healed, playing basketball again without pain.
O: T 37.0 C. Nasal mucosa edematous with clear discharge. Mild maxillary tenderness. Tympanic membranes \
normal. Lungs clear.
A: Viral sinusitis. Symptoms under 10 days without worsening, so antibiotics are not indicated.
P: Saline irrigation, intranasal steroid spray, and acetaminophen as needed. Return if symptoms last \
beyond 10 days, worsen after initial improvement, or fever above 39 C develops. Demonstrated saline \
irrigation technique and advised using distilled or boiled water. Flu vaccine offered and given today. \
Nonsmoker, no alcohol concerns. Discussed hand hygiene and staying home while febrile. Due for \
routine physical next year."""),
    ],
    "g-linh": [
        ("n-linh-1", date(2026, 7, 20), "Maria Reyes, MD", """\
S: 41-year-old woman here for kidney follow-up with a Vietnamese interpreter. Reports fatigue limiting \
her to about two hours of light activity before she must lie down. She stopped her job at a nail salon \
in the spring because she could not get through a shift. Mild ankle swelling by evening. Adherent to \
amlodipine and losartan.
O: BP 146/90. Trace bilateral ankle edema. Conjunctival pallor. Labs: creatinine 2.9, eGFR 22, \
potassium 5.1, hemoglobin 9.8.
A: Chronic kidney disease stage 4, eGFR 22, secondary to hypertensive nephropathy. Anemia of chronic \
kidney disease. Hypertension, above goal.
P: Increase amlodipine to 10 mg. Low-potassium diet teaching with interpreter. Refer to nephrology for \
dialysis planning and transplant evaluation. Avoid NSAIDs. Recheck labs in 6 weeks."""),
        ("n-linh-2", date(2026, 11, 16), "Maria Reyes, MD", """\
S: Follow-up after nephrology visit. Transplant evaluation started. Still tires quickly and needs to \
rest in the afternoon most days. Appetite fair. No shortness of breath or confusion. Vietnamese \
interpreter present.
O: BP 132/84. Trace ankle edema. Labs: creatinine 3.3, eGFR 19, potassium 4.8, hemoglobin 9.4.
A: Chronic kidney disease stage 4, declining, eGFR 19. Anemia of chronic kidney disease, starting \
treatment per nephrology. Hypertension, improved on current regimen.
P: Continue amlodipine and losartan. Erythropoiesis-stimulating agent per nephrology. Arteriovenous \
fistula planning discussed; she prefers to begin with the left arm. Hepatitis B vaccine series started. \
Reviewed low-potassium and low-phosphorus food choices with the interpreter and gave a printed list in \
Vietnamese. Advised to avoid NSAIDs and herbal supplements. Return in 2 months with labs."""),
    ],
    "g-karen": [
        ("n-karen-1", date(2026, 5, 12), "James Okafor, MD", """\
S: 47-year-old woman here for medication check. Mood good, sleeping 7 hours, enjoying time with her \
sister and her garden. Working full time as a dental office manager. No thoughts of self-harm. Tolerating \
sertraline without side effects.
O: Well groomed, good eye contact, euthymic affect, linear thought process. PHQ-9 score 3.
A: Major depressive disorder, recurrent, in sustained remission on sertraline 100 mg daily.
P: Continue sertraline 100 mg daily. Discussed that she would like to stay on medication through the \
winter, given past seasonal relapses; will revisit tapering in the spring. Continue therapy check-ins \
every other month. Reviewed early warning signs of relapse, including sleep changes and withdrawal \
from activities, and how to reach the clinic. Return in 5 months, sooner if mood changes."""),
        ("n-karen-2", date(2026, 10, 8), "James Okafor, MD", """\
S: Routine follow-up before winter. Mood stable. Recently promoted at work and continues working full \
time. Exercising 3 times a week. Sleep and appetite normal. No thoughts of self-harm. Continues therapy \
check-ins and finds them helpful. No alcohol use. Taking sertraline every morning without missed doses.
O: Well groomed, euthymic, reactive affect, organized thoughts, good insight. PHQ-9 score 2, GAD-7 \
score 1. BP 118/76, weight stable.
A: Major depressive disorder, recurrent, in remission. Stable on current dose.
P: Continue sertraline 100 mg daily through the winter. Light box use discussed for the darker months. \
Flu vaccine given. Plan to discuss a gradual taper at the spring visit if mood remains stable. Crisis line number \
reconfirmed in her phone. Return in 6 months."""),
    ],
    "g-omar": [
        ("n-omar-1", date(2026, 3, 3), "Anita Patel, MD", """\
S: 35-year-old man here for an annual physical. Works part time as a delivery driver and plays soccer on \
weekends. No complaints. Nonsmoker, drinks 2 to 3 beers per week. No family history of early heart \
disease. No medications.
O: BP 122/78, HR 64, BMI 26. Heart regular, lungs clear, abdomen soft. Skin exam normal.
A: Healthy adult. Overweight by BMI.
P: Fasting lipid panel and A1c ordered. Tetanus booster given. Discussed diet, fewer sugary drinks \
during delivery shifts, and continuing regular exercise. Sunscreen use for time outdoors while driving. \
Seat belt use and phone-free driving encouraged. Dental visit recommended; last cleaning over two years ago. Hepatitis C and HIV screening offered once; patient \
accepted, drawn with other labs. Return in one year or as needed."""),
        ("n-omar-2", date(2026, 7, 1), "Anita Patel, MD", """\
S: Four days of nasal congestion, sneezing, and sinus pressure. No fever, cough, or ear pain. Two \
coworkers had colds last week. Lipid panel and A1c from March were normal. Still driving deliveries part \
time. Has tried an over-the-counter decongestant with some relief.
O: T 36.9 C, HR 68. Boggy nasal mucosa with clear discharge. No sinus tenderness. Tympanic membranes \
normal. Oropharynx clear. Lungs clear. No cervical lymphadenopathy.
A: Viral sinusitis. No features of bacterial infection.
P: Saline rinses and intranasal steroid spray. Acetaminophen as needed. Limit decongestant to 3 days. \
Return if symptoms last beyond 10 days, worsen after improving, or fever develops. No work restrictions \
needed. Hand hygiene reviewed with patient. Fluids and rest encouraged. Routine physical due again next March."""),
    ],
    "g-bea": [
        ("n-bea-1", date(2026, 4, 22), "Anita Patel, MD", """\
S: 60-year-old woman here for blood pressure follow-up. Checking readings at home, mostly 130s over 80s. \
Retired from retail two years ago. Patient's mother has severe arthritis and now lives in an assisted \
living facility nearby; patient visits her on weekends. No chest pain, headaches, or vision changes. Takes lisinopril each morning and rarely misses a dose. \
Nonsmoker, no alcohol.
O: BP 132/82, HR 70, BMI 28. Heart regular, no murmurs. Lungs clear. No edema.
A: Essential hypertension, controlled on lisinopril. Family history of arthritis, noted.
P: Continue lisinopril 10 mg daily. Basic metabolic panel today. Encouraged continued home monitoring \
with a log to bring to visits, and reduced sodium intake. A1c ordered given family history of diabetes. \
Return in 6 months."""),
        ("n-bea-2", date(2026, 10, 14), "Anita Patel, MD", """\
S: Blood pressure and prediabetes follow-up. Walks 30 minutes most mornings with a neighbor. Denies joint \
pain or difficulty walking. Recent A1c 6.0. Eating more vegetables since the last visit and cooking at \
home more often. Home blood pressure log shows readings in the 120s over 70s. No medication side effects.
O: BP 128/80, HR 68, BMI 27.5, down 2 pounds. Heart and lungs normal. Feet with intact sensation and \
normal pulses.
A: Essential hypertension, controlled. Prediabetes, stable.
P: Continue lisinopril 10 mg daily. Diabetes prevention program information provided; she will call to \
enroll. Repeat A1c in 6 months. Flu and shingles vaccines given today. Colonoscopy due; referral placed. \
Mammogram up to date. Next blood pressure check with the nurse in 3 months. Return in 6 months."""),
    ],
}


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def locate(text: str, quote: str) -> tuple[int, int]:
    """Offsets of quote in text; fails if missing or ambiguous. The only way offsets are produced."""
    start = text.find(quote)
    if start == -1:
        raise ValueError(f"quote not found: {quote!r}")
    if text.find(quote, start + 1) != -1:
        raise ValueError(f"quote is ambiguous: {quote!r}")
    return start, start + len(quote)


def build_notes(pid: str) -> list[Note]:
    return [Note(id=nid, patient_id=pid, date=d, author=a, text=t) for nid, d, a, t in NOTES[pid]]


def claim(notes: list[Note], cid: str, note_id: str, quote: str, category: str, condition: str,
          impairs: Tri, verified: bool = True, reason: str | None = None) -> Claim:
    note = next(n for n in notes if n.id == note_id)
    start, end = locate(note.text, quote)
    return Claim(id=cid, patient_id=note.patient_id, note_id=note_id, category=category, condition=condition,
                 qualifying_category=True, significantly_impairs=impairs, quote=quote, start=start, end=end,
                 verified=verified, verifier_reason=reason)


def fact(fid: str, pid: str, key: str, value, source: Source, source_ref: dict, quote: str | None = None) -> Fact:
    return Fact(id=fid, patient_id=pid, key=key, value=value, source=source, source_ref=source_ref, quote=quote,
                recorded_at=RUN_AT, rule_pack_version=PACK)


def span_fact(fid: str, key: str, value, c: Claim) -> Fact:
    return fact(fid, c.patient_id, key, value, Source.note_span,
                {"note_id": c.note_id, "start": c.start, "end": c.end, "claim_id": c.id}, quote=c.quote)


def billing_fact(fid: str, pid: str, key: str, value, dx: dict) -> Fact:
    return fact(fid, pid, key, value, Source.billing_code,
                {"claim_id": dx["claim_id"], "code": dx["code"], "sequence": dx["sequence"]})


def race_fact(fid: str, pid: str) -> Fact:
    return fact(fid, pid, "ai_an", False, Source.structured_record, {"resource": "Patient", "field": "race"})


def det(pid: str, channel: str, status: str, rule_ids: list[str], facts: list[Fact]) -> Determination:
    return Determination(patient_id=pid, channel=channel, status=status, rule_ids=rule_ids,
                         fact_ids=[f.id for f in facts], rule_pack_version=PACK)


def dx(claim_id: str, d: date, code: str, display: str, sequence: int) -> dict:
    return {"claim_id": claim_id, "date": d.isoformat(), "code": code, "system": "SNOMED", "display": display,
            "sequence": sequence}


def event(kind: str, detail: str) -> dict:
    return {"at": RUN_AT.isoformat(), "kind": kind, "detail": detail}


# ---------------------------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------------------------

def rosa() -> tuple[Case, list[Note]]:
    pid = "g-rosa"
    notes = build_notes(pid)
    billed = [
        dx("cl-rosa-1", date(2026, 4, 8), "10509002", "Acute bronchitis", 1),
        dx("cl-rosa-1", date(2026, 4, 8), "44054006", "Diabetes mellitus type 2", 2),
        dx("cl-rosa-2", date(2026, 8, 19), "49727002", "Cough", 1),
        dx("cl-rosa-2", date(2026, 8, 19), "368581000119106", "Neuropathy due to type 2 diabetes mellitus", 2),
        dx("cl-rosa-3", date(2026, 12, 2), "49727002", "Cough", 1),
    ]
    claims = [
        claim(notes, "c-rosa-1", "n-rosa-2", "Type 2 diabetes mellitus with diabetic peripheral neuropathy",
              "physical_disability", "diabetic peripheral neuropathy", Tri.unknown),
        claim(notes, "c-rosa-2", "n-rosa-2",
              "Reports numbness and burning in both feet, worse at night, present for over a year.",
              "physical_disability", "diabetic peripheral neuropathy", Tri.unknown),
        claim(notes, "c-rosa-3", "n-rosa-3", "Diabetic peripheral neuropathy on gabapentin",
              "physical_disability", "diabetic peripheral neuropathy", Tri.unknown),
    ]
    f_race = race_fact("f-rosa-1", pid)
    f_qual = span_fact("f-rosa-2", "qualifying_condition", True, claims[0])
    missing = [
        MissingFact(id="m-rosa-1", patient_id=pid, key="standing_tolerance_minutes", holder=Holder.patient,
                    unlocks_rule="medically_frail",
                    why="Neuropathy is in her notes, but nothing says how it limits her. "
                        "If she can't stand for long, she qualifies as medically frail.",
                    question=Q_STANDING),
        MissingFact(id="m-rosa-2", patient_id=pid, key="limitation_attested", holder=Holder.clinician,
                    unlocks_rule="medically_frail",
                    why="Once Rosa describes her limits, Dr. Patel confirms the record supports them.",
                    question=q_limitation("diabetic peripheral neuropathy", "Rosa")),
    ]
    case = Case(
        patient_id=pid, display_name="Rosa Delgado", age=52, language="es",
        email="rosa.delgado@example.com", phone="+1-555-0101",
        clinic_id="clinic-mission", clinician_name="Dr. Anita Patel",
        renewal_date=date(2027, 3, 4), bucket=Bucket.ONE_AWAY, fragile=False, status=CaseStatus.needs_action,
        determination_a=det(pid, "A", "not_determined", [], [f_race]),
        determination_final=det(pid, "final", "not_determined", [], [f_race, f_qual]),
        claims=claims, dropped_claims=[], facts=[f_race, f_qual], missing=missing, billed_dx_12mo=billed,
        events=[event("pipeline_run", "State check could not determine: no primary diagnosis on the code list. "
                                      "Neuropathy found in notes; how it limits her is not described.")],
    )
    return case, notes


def marcus() -> tuple[Case, list[Note]]:
    pid = "g-marcus"
    notes = build_notes(pid)
    billed = [
        dx("cl-marcus-1", date(2026, 6, 11), "44465007", "Sprain of ankle", 1),
        dx("cl-marcus-1", date(2026, 6, 11), "185086009", "Chronic obstructive bronchitis", 2),
        dx("cl-marcus-2", date(2026, 8, 27), "44465007", "Sprain of ankle", 1),
        dx("cl-marcus-2", date(2026, 8, 27), "185086009", "Chronic obstructive bronchitis", 2),
    ]
    claims = [
        claim(notes, "c-marcus-1", "n-marcus-2",
              "Severe COPD, GOLD stage 3, with frequent exacerbations and exertional hypoxemia.",
              "serious_complex_medical", "severe COPD", Tri.unknown),
        claim(notes, "c-marcus-2", "n-marcus-2",
              "Reports shortness of breath with minimal exertion such as dressing or showering.",
              "serious_complex_medical", "severe COPD", Tri.true),
        claim(notes, "c-marcus-3", "n-marcus-2",
              "Cannot walk more than half a block without stopping to rest.",
              "serious_complex_medical", "severe COPD", Tri.true),
    ]
    f_race = race_fact("f-marcus-1", pid)
    f_qual = span_fact("f-marcus-2", "qualifying_condition", True, claims[0])
    f_imp = span_fact("f-marcus-3", "significantly_impairs", True, claims[2])
    missing = [
        MissingFact(id="m-marcus-1", patient_id=pid, key="limitation_attested", holder=Holder.clinician,
                    unlocks_rule="medically_frail",
                    why="His notes already show severe COPD that stops him walking half a block. "
                        "Dr. Okafor only needs to confirm and sign.",
                    question=q_limitation("severe COPD", "Marcus")),
    ]
    case = Case(
        patient_id=pid, display_name="Marcus Webb", age=58, language="en",
        email="marcus.webb@example.com", phone="+1-555-0102",
        clinic_id="clinic-eastside", clinician_name="Dr. James Okafor",
        renewal_date=date(2027, 2, 26), bucket=Bucket.PROVABLE, fragile=False, status=CaseStatus.needs_action,
        determination_a=det(pid, "A", "not_determined", [], [f_race]),
        determination_final=det(pid, "final", "exempt", ["medically_frail"], [f_race, f_qual, f_imp]),
        claims=claims, dropped_claims=[], facts=[f_race, f_qual, f_imp], missing=missing, billed_dx_12mo=billed,
        events=[event("pipeline_run", "State check could not determine: primary diagnosis was an ankle sprain. "
                                      "Notes show severe COPD limiting walking; ready for clinician signature.")],
    )
    return case, notes


def deshawn() -> tuple[Case, list[Note]]:
    pid = "g-deshawn"
    notes = build_notes(pid)
    billed = [
        dx("cl-deshawn-1", date(2026, 5, 2), "70704007", "Sprain of wrist", 1),
        dx("cl-deshawn-2", date(2026, 9, 14), "444814009", "Viral sinusitis", 1),
    ]
    f_race = race_fact("f-deshawn-1", pid)
    f_income = fact("f-deshawn-2", pid, "monthly_income", 0, Source.external_db,
                    {"db": "state_wage_records", "record_id": "wr-deshawn-2027-01"})
    f_foster = fact("f-deshawn-3", pid, "former_foster_youth", False, Source.external_db,
                    {"db": "child_welfare", "record_id": "cw-deshawn"})
    facts = [f_race, f_income, f_foster]
    missing = [
        MissingFact(id="m-deshawn-1", patient_id=pid, key="enrolled_half_time_school", holder=Holder.database,
                    database="student_enrollment", unlocks_rule="school",
                    why="No work hours or wages on file, but he may be in school. "
                        "The student enrollment database can confirm it without contacting him.",
                    question={"en": "Is Deshawn enrolled at least half time in school?"}),
    ]
    case = Case(
        patient_id=pid, display_name="Deshawn Price", age=23, language="en",
        email="deshawn.price@example.com", phone="+1-555-0103",
        clinic_id="clinic-valley", clinician_name="Dr. Maria Reyes",
        renewal_date=date(2027, 3, 10), bucket=Bucket.ONE_AWAY, fragile=False, status=CaseStatus.needs_action,
        determination_a=det(pid, "A", "not_determined", [], [f_race, f_income]),
        determination_final=det(pid, "final", "not_determined", [], facts),
        claims=[], dropped_claims=[], facts=facts, missing=missing, billed_dx_12mo=billed,
        events=[event("pipeline_run", "State check could not determine: no wages, hours or exemption on file. "
                                      "Student enrollment not yet checked.")],
    )
    return case, notes


def linh() -> tuple[Case, list[Note]]:
    pid = "g-linh"
    notes = build_notes(pid)
    billed = [
        dx("cl-linh-1", date(2026, 3, 30), "431857002", "Chronic kidney disease stage 4", 1),
        dx("cl-linh-1", date(2026, 3, 30), "59621000", "Essential hypertension", 2),
        dx("cl-linh-2", date(2026, 7, 20), "431857002", "Chronic kidney disease stage 4", 1),
        dx("cl-linh-3", date(2026, 11, 16), "431857002", "Chronic kidney disease stage 4", 1),
    ]
    claims = [
        claim(notes, "c-linh-1", "n-linh-1",
              "Chronic kidney disease stage 4, eGFR 22, secondary to hypertensive nephropathy.",
              "serious_complex_medical", "chronic kidney disease stage 4", Tri.unknown),
        claim(notes, "c-linh-2", "n-linh-1",
              "Reports fatigue limiting her to about two hours of light activity before she must lie down.",
              "serious_complex_medical", "chronic kidney disease stage 4", Tri.true),
    ]
    latest = billed[3]
    f_qual_a = billing_fact("f-linh-1", pid, "qualifying_condition", True, latest)
    f_imp_a = billing_fact("f-linh-2", pid, "significantly_impairs", True, latest)
    f_race = race_fact("f-linh-3", pid)
    f_qual_b = span_fact("f-linh-4", "qualifying_condition", True, claims[0])
    f_imp_b = span_fact("f-linh-5", "significantly_impairs", True, claims[1])
    facts = [f_qual_a, f_imp_a, f_race, f_qual_b, f_imp_b]
    case = Case(
        patient_id=pid, display_name="Linh Tran", age=41, language="vi",
        email="linh.tran@example.com", phone="+1-555-0104",
        clinic_id="clinic-valley", clinician_name="Dr. Maria Reyes",
        renewal_date=date(2027, 3, 22), bucket=Bucket.SAFE, fragile=False, status=CaseStatus.no_action,
        determination_a=det(pid, "A", "exempt", ["medically_frail"], [f_qual_a, f_imp_a, f_race]),
        determination_final=det(pid, "final", "exempt", ["medically_frail"], facts),
        claims=claims, dropped_claims=[], facts=facts, missing=[], billed_dx_12mo=billed,
        events=[event("pipeline_run", "State exempts on primary diagnosis: chronic kidney disease stage 4. "
                                      "Notes support it. No action needed.")],
    )
    return case, notes


def karen() -> tuple[Case, list[Note]]:
    pid = "g-karen"
    notes = build_notes(pid)
    billed = [
        dx("cl-karen-1", date(2026, 5, 12), "370143000", "Major depressive disorder", 1),
        dx("cl-karen-2", date(2026, 10, 8), "370143000", "Major depressive disorder", 1),
    ]
    claims = [
        claim(notes, "c-karen-1", "n-karen-1",
              "Major depressive disorder, recurrent, in sustained remission on sertraline 100 mg daily.",
              "serious_mental_illness", "major depressive disorder", Tri.false),
    ]
    latest = billed[1]
    f_qual_a = billing_fact("f-karen-1", pid, "qualifying_condition", True, latest)
    f_imp_a = billing_fact("f-karen-2", pid, "significantly_impairs", True, latest)
    f_race = race_fact("f-karen-3", pid)
    facts = [f_qual_a, f_imp_a, f_race]
    case = Case(
        patient_id=pid, display_name="Karen Hollis", age=47, language="en",
        email="karen.hollis@example.com", phone="+1-555-0105",
        clinic_id="clinic-eastside", clinician_name="Dr. James Okafor",
        renewal_date=date(2027, 4, 2), bucket=Bucket.SAFE, fragile=True, status=CaseStatus.no_action,
        determination_a=det(pid, "A", "exempt", ["medically_frail"], facts),
        determination_final=det(pid, "final", "exempt", ["medically_frail"], facts),
        claims=claims, dropped_claims=[], facts=facts, missing=[], billed_dx_12mo=billed,
        events=[event("pipeline_run", "State exempts on primary diagnosis: major depressive disorder. "
                                      "No sentence in 2 notes supports an impairment. Flagged fragile.")],
    )
    return case, notes


def omar() -> tuple[Case, list[Note]]:
    pid = "g-omar"
    notes = build_notes(pid)
    billed = [
        dx("cl-omar-1", date(2026, 7, 1), "444814009", "Viral sinusitis", 1),
    ]
    f_income = fact("f-omar-1", pid, "monthly_income", 920, Source.external_db,
                    {"db": "state_wage_records", "record_id": "wr-omar-2027-01"})
    f_race = race_fact("f-omar-2", pid)
    facts = [f_income, f_race]
    case = Case(
        patient_id=pid, display_name="Omar Haddad", age=35, language="en",
        email="omar.haddad@example.com", phone="+1-555-0106",
        clinic_id="clinic-mission", clinician_name="Dr. Anita Patel",
        renewal_date=date(2027, 4, 8), bucket=Bucket.SAFE, fragile=False, status=CaseStatus.no_action,
        determination_a=det(pid, "A", "compliant", ["income"], facts),
        determination_final=det(pid, "final", "compliant", ["income"], facts),
        claims=[], dropped_claims=[], facts=facts, missing=[], billed_dx_12mo=billed,
        events=[event("pipeline_run", "Meets the requirement through income: state wage records show $920/month. "
                                      "Never contacted.")],
    )
    return case, notes


def bea() -> tuple[Case, list[Note]]:
    pid = "g-bea"
    notes = build_notes(pid)
    billed = [
        dx("cl-bea-1", date(2026, 4, 22), "59621000", "Essential hypertension", 1),
        dx("cl-bea-2", date(2026, 10, 14), "59621000", "Essential hypertension", 1),
        dx("cl-bea-2", date(2026, 10, 14), "714628002", "Prediabetes", 2),
    ]
    dropped = [
        claim(notes, "c-bea-1", "n-bea-1", "Patient's mother has severe arthritis",
              "physical_disability", "severe arthritis", Tri.unknown, verified=False,
              reason="The span describes the patient's mother, not the patient."),
    ]
    f_race = race_fact("f-bea-1", pid)
    f_income = fact("f-bea-2", pid, "monthly_income", 0, Source.external_db,
                    {"db": "state_wage_records", "record_id": "wr-bea-2027-01"})
    f_snap = fact("f-bea-3", pid, "snap_tanf_work_compliant", False, Source.external_db,
                  {"db": "snap", "record_id": "snap-bea"})
    f_child = fact("f-bea-4", pid, "dependent_child_13_or_under", False, Source.structured_record,
                   {"resource": "Household", "field": "members"})
    facts = [f_race, f_income, f_snap, f_child]
    case = Case(
        patient_id=pid, display_name="Bea Knox", age=60, language="en",
        email="bea.knox@example.com", phone="+1-555-0107",
        clinic_id="clinic-mission", clinician_name="Dr. Anita Patel",
        renewal_date=date(2027, 3, 15), bucket=Bucket.NO_PATH, fragile=False, status=CaseStatus.no_action,
        determination_a=det(pid, "A", "not_determined", [], facts),
        determination_final=det(pid, "final", "not_determined", [], facts),
        claims=[], dropped_claims=dropped, facts=facts, missing=[], billed_dx_12mo=billed,
        events=[event("pipeline_run", "No exemption within one fact. Verifier dropped 1 claim about a relative. "
                                      "Needs help reporting hours.")],
    )
    return case, notes


BUILDERS = [rosa, marcus, deshawn, linh, karen, omar, bea]


def build() -> list[dict]:
    """Golden cases in the fixture file shape: each entry is a full Case plus its notes."""
    out = []
    for builder in BUILDERS:
        case, notes = builder()
        entry = case.model_dump(mode="json")
        entry["notes"] = [n.model_dump(mode="json") for n in notes]
        out.append(entry)
    return out


def main() -> None:
    FIXTURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURES_PATH.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(BUILDERS)} golden cases to {FIXTURES_PATH}")


if __name__ == "__main__":
    main()
