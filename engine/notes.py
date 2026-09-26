"""Generated progress notes with known labels. Deterministic, no LLM.

Each patient gets 2-4 S/O/A/P notes dated to their real visits in the lookback window (telephone notes fill
in when they had fewer than two). Templates only ever name benign problems. Every mention of a qualifying
condition, a functional limitation, SUD treatment, or a distractor comes verbatim from
data/impairment_library.yaml and is recorded in data/truth/labels.jsonl with its exact offsets. So the labels
are complete: a claim Channel B finds that matches no label is a false positive.

What a note says is decided by the truth (engine/truth.py), never by the other way round.
"""
import json
from datetime import timedelta
from functools import cache

import yaml
from pydantic import BaseModel

from engine.cohort import BENIGN_CHRONIC, Encounter, Patient, _unit
from engine.config import CLINICS, IMPAIRMENT_LIBRARY_PATH, LABELS_PATH, LOOKBACK_START
from engine.golden import locate
from engine.models import Note
from engine.truth import Truth

MIN_WORDS, MAX_WORDS = 80, 250      # generated notes; hand-written golden notes are 120-250

LANGUAGE_NAMES = {"es": "Spanish", "vi": "Vietnamese", "zh": "Chinese", "ko": "Korean", "hi": "Hindi",
                  "de": "German", "fr": "French", "haw": "Hawaiian", "ru": "Russian", "ar": "Arabic",
                  "tl": "Tagalog", "ja": "Japanese", "pt": "Portuguese", "it": "Italian", "pl": "Polish"}


class Label(BaseModel):
    note_id: str
    patient_id: str
    start: int
    end: int
    category: str | None
    qualifying: bool                  # does this sentence establish a current qualifying condition of the patient
    impairs: str                      # "true" | "false" | "unknown": what the sentence itself says about function
    polarity: str                     # positive | unstated | remission | function_normal | negated | resolved |
                                      # family | past | hypothetical
    group: str | None = None
    fact_key: str | None = None       # set when the sentence evidences a registry fact other than frailty
    library_id: str | None = None
    text: str                         # the sentence as injected; note.text[start:end] must equal it


# ---------------------------------------------------------------------------------------------
# Visit templates. Slots: {age} {person} {reason} {bp} {hr} {temp} {spo2} {bmi} {days} {weeks}
# ---------------------------------------------------------------------------------------------

T = {
    "wellness": {
        "S": ["{age}-year-old {person} here for an annual physical.", "No acute complaints today.",
              "Sleeping and eating well, with no recent illnesses or hospital visits.",
              "No fevers or unintended weight change.",
              "Reviewed which preventive screenings are due this year."],
        "O": ["BP {bp}, HR {hr}, BMI {bmi}.", "Well appearing, in no distress.",
              "Heart regular without murmur, lungs clear bilaterally.", "Abdomen soft and nontender, no masses.",
              "Skin without concerning lesions."],
        "A": ["Adult wellness visit."],
        "P": ["Fasting lipid panel and A1c ordered.", "Influenza vaccine offered.",
              "Discussed diet, physical activity, sleep and sun protection.",
              "Depression and alcohol use screening completed.", "Return in one year or as needed."],
    },
    "prenatal": {
        "S": ["{age}-year-old woman here for a prenatal visit.", "Nausea from early pregnancy is improving.",
              "Taking prenatal vitamins daily.", "Denies bleeding, leakage of fluid or cramping.",
              "Reports normal fetal movement when expected for dates."],
        "O": ["BP {bp}, HR {hr}.", "Fetal heart tones present in the normal range.",
              "Fundal height appropriate for dates.", "No edema of the hands or face.", "Urine negative for protein."],
        "A": ["Intrauterine pregnancy, routine prenatal care."],
        "P": ["Continue prenatal vitamins.", "Routine prenatal labs reviewed with the patient.",
              "Pregnancy warning signs reviewed, including headache, vision changes and bleeding.",
              "Glucose screening to be scheduled at the appropriate week.", "Return in 4 weeks."],
    },
    "postnatal": {
        "S": ["{age}-year-old woman here for a postpartum visit.", "Recovering well after delivery.",
              "Feeding the baby without difficulty.", "Bleeding has tapered to light spotting.",
              "Mood reviewed and sleeping when the baby sleeps."],
        "O": ["BP {bp}, HR {hr}.", "Abdomen soft, uterus firm and nontender.", "Incision and perineum healing well.",
              "Breasts without redness or tenderness."],
        "A": ["Postpartum follow-up, recovering as expected."],
        "P": ["Contraception options discussed and questions answered.", "Postpartum depression screening completed.",
              "Iron supplement continued for another month.", "Return as needed."],
    },
    "contraception": {
        "S": ["{age}-year-old {person} here for contraception care.", "No side effects from the current method.",
              "No missed doses reported.", "No leg swelling or calf pain.",
              "Satisfied with the current method and wishes to continue."],
        "O": ["BP {bp}, HR {hr}, BMI {bmi}.", "Well appearing, in no distress.", "Heart regular, lungs clear."],
        "A": ["Contraception management, no contraindications to the current method."],
        "P": ["Continue current method.", "Twelve-month refill sent to pharmacy.",
              "Reviewed backup options and emergency contraception.", "Sexually transmitted infection screening offered.",
              "Return in 12 months."],
    },
    "dental": {
        "S": ["{age}-year-old {person} seen for gum bleeding and tooth sensitivity.",
              "Brushing once daily, flossing rarely.", "No fever or facial swelling.",
              "Last dental cleaning was more than two years ago."],
        "O": ["BP {bp}, HR {hr}.", "Gingival erythema with plaque buildup along the gumline.",
              "No fluctuance or abscess.", "No cervical lymphadenopathy."],
        "A": ["{reason}."],
        "P": ["Referred to the community dental clinic for cleaning and evaluation.",
              "Oral hygiene reviewed, including twice-daily brushing and daily flossing.",
              "Chlorhexidine rinse prescribed for two weeks.", "Return if swelling or fever develops."],
    },
    "sinusitis": {
        "S": ["{age}-year-old {person} with {days} days of nasal congestion, facial pressure and clear nasal "
              "discharge.", "No fever.", "Similar symptoms in others at home.", "Mild headache, worse bending forward.",
              "No ear pain; breathing comfortable at rest."],
        "O": ["T {temp} C, HR {hr}, SpO2 {spo2}% on room air.", "Nasal mucosa edematous with clear discharge.",
              "Mild maxillary tenderness.", "Tympanic membranes normal.", "Lungs clear."],
        "A": ["{reason}."],
        "P": ["Saline irrigation and intranasal steroid spray.", "Acetaminophen as needed.",
              "Antibiotics not indicated at this time.",
              "Return if symptoms last beyond 10 days or worsen after improving."],
    },
    "pharyngitis": {
        "S": ["{age}-year-old {person} with {days} days of sore throat and painful swallowing.",
              "Low-grade temperature at home.", "No cough or nasal congestion.",
              "Able to drink fluids without difficulty."],
        "O": ["T {temp} C, HR {hr}.", "Pharyngeal erythema with tonsillar swelling.",
              "Tender anterior cervical lymph nodes.", "No uvular deviation or drooling.", "Lungs clear."],
        "A": ["{reason}."],
        "P": ["Rapid strep test result reviewed with the patient.", "Warm salt-water gargles and acetaminophen.",
              "Antibiotic course prescribed if the culture returns positive.",
              "Return if swallowing liquids or breathing becomes difficult."],
    },
    "bronchitis": {
        "S": ["{age}-year-old {person} with {days} days of productive cough and chest tightness.",
              "Breathing comfortable at rest.", "Recent cold in the household.",
              "No calf pain or leg swelling."],
        "O": ["T {temp} C, HR {hr}, SpO2 {spo2}% on room air.", "Scattered rhonchi, no focal consolidation.",
              "No accessory muscle use.", "Heart regular."],
        "A": ["{reason}, likely viral."],
        "P": ["Supportive care with fluids and honey.", "Albuterol as needed for chest tightness.",
              "Cough may last up to three weeks; antibiotics not indicated.",
              "Return if fever persists or breathing worsens."],
    },
    "uti": {
        "S": ["{age}-year-old {person} with {days} days of burning with urination and urinary frequency.",
              "No fever, flank pain, nausea or vomiting.", "No blood in the urine.",
              "Similar episode about a year ago that resolved with antibiotics."],
        "O": ["T {temp} C, HR {hr}.", "Mild suprapubic tenderness, no costovertebral angle tenderness.",
              "Urinalysis positive for leukocyte esterase and nitrites."],
        "A": ["{reason}."],
        "P": ["Nitrofurantoin for 5 days.", "Urine culture sent.", "Increase fluid intake.",
              "Return if fever or flank pain develops."],
    },
    "injury": {
        "S": ["{age}-year-old {person} seen after an injury: {reason_lower}.",
              "Pain controlled with over-the-counter medication.", "Pain localized to the injured area.",
              "Injury occurred at home; no loss of consciousness."],
        "O": ["BP {bp}, HR {hr}.", "Localized tenderness and swelling.", "Neurovascularly intact distally.",
              "Skin otherwise intact."],
        "A": ["{reason}."],
        "P": ["Supportive care with ice and elevation.", "Ibuprofen with food as needed for pain.",
              "Gradual return to usual activity over the next two weeks.", "Return if pain or swelling worsens."],
    },
    "otitis": {
        "S": ["{age}-year-old {person} with {days} days of ear pain and muffled hearing.", "No drainage.",
              "Recent upper respiratory infection.", "No dizziness."],
        "O": ["T {temp} C, HR {hr}.", "Tympanic membrane erythematous and bulging.",
              "Opposite ear normal.", "Oropharynx clear."],
        "A": ["{reason}."],
        "P": ["Amoxicillin for 7 days.", "Acetaminophen for pain.", "Avoid water in the ear until symptoms resolve.",
              "Return if not improving in 3 days."],
    },
    "benign_chronic": {
        "S": ["{age}-year-old {person} here for follow-up of {reason_lower}.",
              "Taking medications as prescribed.", "No new concerns since the last visit.",
              "No medication side effects reported.", "Diet reviewed; cooking at home most days."],
        "O": ["BP {bp}, HR {hr}, BMI {bmi}.", "Heart regular, lungs clear.", "No peripheral edema."],
        "A": ["{reason}, stable."],
        "P": ["Continue current regimen.", "Labs ordered before the next visit.",
              "Discussed regular physical activity as tolerated.", "Return in 3 months."],
    },
    "screening": {
        "S": ["{age}-year-old {person} here to discuss colon cancer screening.", "No change in bowel habits.",
              "No blood in the stool, abdominal pain or weight loss.", "No relevant family history."],
        "O": ["BP {bp}, HR {hr}, BMI {bmi}.", "Abdomen soft and nontender.", "No masses palpated."],
        "A": ["Colorectal cancer screening, average risk."],
        "P": ["Colonoscopy referral placed.", "Bowel preparation instructions provided in writing.",
              "Stool-based testing discussed as an alternative.", "Return as needed."],
    },
    "chronic_neutral": {
        "S": ["{age}-year-old {person} here for scheduled follow-up of chronic conditions.",
              "Reviewed medications and recent results together.", "No new symptoms since the last visit.",
              "Taking medications as prescribed.", "No emergency visits since last seen."],
        "O": ["BP {bp}, HR {hr}, BMI {bmi}.", "No acute distress.", "Heart regular.", "Lungs clear to auscultation."],
        "A": ["Chronic condition follow-up; problem list reviewed."],
        "P": ["Continue current treatment plan.", "Coordinate with specialists as needed.",
              "Labs to be drawn before the next visit.", "Return in {weeks} weeks."],
    },
    "ed": {
        "S": ["{age}-year-old {person} evaluated in the emergency department.",
              "Symptoms began earlier the same day.", "Emergency department records reviewed at this follow-up.",
              "Feeling better since discharge."],
        "O": ["BP {bp}, HR {hr}, SpO2 {spo2}% on room air.", "Alert and oriented, in no acute distress.",
              "Heart regular, lungs clear."],
        "A": ["Emergency department visit, stable after discharge."],
        "P": ["Reviewed discharge instructions and return precautions.", "Medications from the visit reconciled.",
              "Follow up with primary care within one week."],
    },
    "phone": {
        "S": ["Telephone encounter.", "Patient called to review recent results and current medications.",
              "Identity confirmed with two identifiers.", "Ongoing symptoms reviewed during the call.",
              "Asked about scheduling the next routine visit."],
        "O": ["No vital signs; telephone visit.", "Speaking in full sentences, no distress audible."],
        "A": ["Care coordination call."],
        "P": ["Results explained in plain language.", "Refills confirmed with the pharmacy.",
              "Next clinic visit scheduled.", "Patient verbalized understanding and repeated the plan back.",
              "Routed to the primary clinician for review."],
    },
    "phone_refill": {
        "S": ["Telephone encounter for a medication refill request.", "Patient reports taking medications as prescribed.",
              "Pharmacy on file confirmed.", "Asked about side effects; none reported.",
              "Would like to keep the current doses."],
        "O": ["No vital signs; telephone visit.", "Last clinic vital signs and labs reviewed in the chart."],
        "A": ["Medication refill request, appropriate to continue."],
        "P": ["Ninety-day refills sent to the pharmacy.", "Reminded to bring all medication bottles to the next visit.",
              "Labs due before the next visit; order placed.", "Patient verbalized understanding.",
              "Routed to the primary clinician for co-signature."],
    },
}

FILLERS = ["Medication list reconciled.", "Allergies reviewed with no changes.", "After-visit summary provided.",
           "Patient verbalized understanding of the plan.", "Tobacco use screening completed.",
           "Preferred pharmacy confirmed.", "All questions answered.", "Vaccination record reviewed.",
           "Emergency contact information updated.", "Advised to call the clinic with any new concerns.",
           "Clinic after-hours number provided.", "Reviewed how to use the patient message line."]

REASON_KINDS = [
    ("sinusitis", ["sinusitis"]), ("pharyngitis", ["pharyngitis", "sore throat"]),
    ("bronchitis", ["acute bronchitis"]), ("uti", ["cystitis", "urinary tract"]), ("otitis", ["otitis"]),
    ("injury", ["sprain", "fracture", "laceration", "injury", "concussion", "burn", "dislocation", "whiplash",
                "tear of"]),
    ("dental", ["gingiv", "teeth", "tooth", "dental", "caries", "molars", "filling"]),
    ("prenatal", ["normal pregnancy"]), ("contraception", ["contraception"]),
    ("screening", ["screening for malignant neoplasm of colon"]),
]

# Non-qualifying chronic problems a template may name, with the phrase used in the note.
BENIGN = BENIGN_CHRONIC


@cache
def library() -> dict:
    return yaml.safe_load(IMPAIRMENT_LIBRARY_PATH.read_text())


def visit_kind(e: Encounter) -> str:
    t = e.type.lower()
    if "prenatal" in t:
        return "prenatal"
    if "postnatal" in t:
        return "postnatal"
    reason = (e.reason or "").lower()
    if e.reason_code in BENIGN:
        return "benign_chronic"
    for kind, words in REASON_KINDS:
        if any(w in reason for w in words):
            return kind
    if e.klass == "EMER":
        return "ed"
    if "check up" in t or "general examination" in t:
        return "wellness"
    return "chronic_neutral"          # anything else is never named: it might be a qualifying condition


# ---------------------------------------------------------------------------------------------
# Rendering with tracked offsets
# ---------------------------------------------------------------------------------------------

class _Draft:
    def __init__(self):
        self.sections: dict[str, list[tuple[str, dict | None]]] = {k: [] for k in "SOAP"}

    def add(self, section: str, text: str, label: dict | None = None) -> None:
        self.sections[section].append((text, label))

    def words(self) -> int:
        return sum(len(t.split()) for s in self.sections.values() for t, _ in s)

    def render(self, note_id: str, patient_id: str) -> tuple[str, list[Label]]:
        text, labels = "", []
        for i, name in enumerate("SOAP"):
            text += ("\n" if i else "") + f"{name}: "
            for j, (sentence, label) in enumerate(self.sections[name]):
                if j:
                    text += " "
                if label is not None:
                    labels.append(Label(note_id=note_id, patient_id=patient_id, start=len(text),
                                        end=len(text) + len(sentence), text=sentence, **label))
                text += sentence
        return text, labels


def _slots(p: Patient) -> list[tuple[Encounter | None, str]]:
    """2-4 (encounter, kind) visits, evenly spread; None encounter = synthesized telephone note."""
    seen, encs = set(), []
    for e in p.encounters:
        if e.date not in seen and e.type != "Death Certification":
            seen.add(e.date)
            encs.append(e)
    k = min(max(len(encs), 2), 4)
    if len(encs) > k:
        encs = [encs[round(i * (len(encs) - 1) / (k - 1))] for i in range(k)]
    slots = [(e, visit_kind(e)) for e in encs]
    i = 0
    while len(slots) < 2:
        i += 1
        day = LOOKBACK_START + timedelta(days=int(_unit("phone", p.id, i) * 360))
        if day in seen:
            continue
        seen.add(day)
        kind = "phone" if sum(k.startswith("phone") for _, k in slots) % 2 == 0 else "phone_refill"
        slots.append((Encounter(id=f"phone-{p.id}-{i}", date=day, klass="VR", type="Telephone encounter",
                                reason_code=None, reason=None), kind))
    return sorted(slots, key=lambda s: s[0].date)


def _fill(template: str, p: Patient, e: Encounter, k: str) -> str:
    u = lambda what: _unit("vitals", p.id, e.id, what)   # noqa: E731
    reason = e.reason or ""
    return template.format(
        age=p.age, person="woman" if p.sex == "female" else "man",
        reason=reason[:1].upper() + reason[1:], reason_lower=reason.lower(),
        bp=f"{110 + int(u('sys') * 40)}/{66 + int(u('dia') * 26)}", hr=60 + int(u("hr") * 36),
        temp=f"{36.6 + (1.3 if k in ('pharyngitis', 'bronchitis', 'otitis') else 0.7) * u('t'):.1f}",
        spo2=95 + int(u("spo2") * 5), bmi=f"{21 + 16 * u('bmi'):.1f}", days=2 + int(u("days") * 8),
        weeks=(4, 6, 8, 12)[int(u("weeks") * 4)],
    )


def generate(p: Patient, truth: Truth) -> tuple[list[Note], list[Label]]:
    lib = library()
    slots = _slots(p)
    n = len(slots)
    pick = lambda key, options: options[int(_unit("notes", p.id, key) * len(options))]   # noqa: E731
    where = lambda key: int(_unit("where", p.id, key) * n)                                # noqa: E731

    # What each note must say, decided from the truth: (note index, section, sentence, label).
    inject: list[tuple[int, str, str, dict]] = []
    minutes = truth.facts.get("standing_tolerance_minutes")
    for g in truth.frail_groups:
        variants = lib["conditions"][g.group]
        base = {"category": g.category, "qualifying": True, "group": g.group}
        first = int(_unit("notes", p.id, g.group, "u") * 2)
        inject.append((where(g.group + "u"), "A", variants["unstated"][first],
                       {**base, "impairs": "unknown", "polarity": "unstated",
                        "library_id": f"{g.group}.unstated.{first}"}))
        if n > 1 and _unit("notes", p.id, g.group, "u2") < 0.5:
            other = (where(g.group + "u") + 1) % n
            inject.append((other, "A", variants["unstated"][1 - first],
                           {**base, "impairs": "unknown", "polarity": "unstated",
                            "library_id": f"{g.group}.unstated.{1 - first}"}))
        if g.documentation == "positive":
            i = int(_unit("notes", p.id, g.group, "p") * 2)
            sentence = variants["positive"][i]
            if "{minutes}" in sentence:
                sentence = sentence.format(minutes=minutes.value)
            inject.append((where(g.group + "p"), "S", sentence,
                           {**base, "impairs": "true", "polarity": "positive",
                            "library_id": f"{g.group}.positive.{i}"}))
    if truth.function_normal_note:
        i = int(_unit("notes", p.id, "fn") * len(lib["function_normal"]))
        inject.append((where("fn"), "S", lib["function_normal"][i],
                       {"category": None, "qualifying": False, "impairs": "false", "polarity": "function_normal",
                        "library_id": f"function_normal.{i}"}))
    if truth.sud_treatment_documented:
        i = int(_unit("notes", p.id, "sudt") * len(lib["sud_treatment"]))
        inject.append((where("sudt"), "S", lib["sud_treatment"][i],
                       {"category": "sud", "qualifying": False, "impairs": "unknown", "polarity": "positive",
                        "fact_key": "in_sud_treatment", "library_id": f"sud_treatment.{i}"}))
    groups = {g.group for g in truth.frail_groups} | ({"sud_treatment"} if truth.facts["in_sud_treatment"].value else set())
    impaired = bool(truth.facts["significantly_impairs"].value)
    allowed = [(i, d) for i, d in enumerate(lib["distractors"])
               if not groups & set(d.get("avoid_groups", [])) and not (impaired and d.get("avoid_if_impaired"))]
    r = _unit("notes", p.id, "distractors")
    for k in range(2 if r < 0.10 else 1 if r < 0.35 else 0):
        i, d = allowed[int(_unit("notes", p.id, "distractor", k) * len(allowed))]
        if any(s == d["text"] for _, _, s, _ in inject):
            continue
        inject.append((where(f"d{k}"), "S", d["text"],
                       {"category": d["category"], "qualifying": False, "impairs": "false",
                        "polarity": d["polarity"], "library_id": f"distractor.{i}"}))

    active_benign = sorted({BENIGN[c.code] for c in p.conditions
                            if c.code in BENIGN and c.active_during(LOOKBACK_START, slots[-1][0].date)})
    clinic = CLINICS[p.clinic_id]
    notes, labels = [], []
    for idx, (e, kind) in enumerate(slots):
        note_id = f"n-{p.id.removeprefix('p-')}-{idx + 1}"
        draft = _Draft()
        tpl = T[kind]
        for sec in "SOAP":
            for sentence in tpl[sec]:
                draft.add(sec, _fill(sentence, p, e, kind))
            if sec == "S" and p.language != "en" and not kind.startswith("phone"):
                draft.add("S", f"Visit conducted with a {LANGUAGE_NAMES.get(p.language, 'professional')} interpreter.")
            for i_note, s_sec, sentence, label in inject:
                if i_note == idx and s_sec == sec:
                    draft.add(sec, sentence, label)
            if sec == "A" and not kind.startswith("phone"):
                for problem in active_benign[:2]:
                    if problem.lower() not in (e.reason or "").lower():
                        draft.add("A", f"{problem}, stable on current plan.")
        f = 0
        order = sorted(range(len(FILLERS)), key=lambda j: _unit("filler", note_id, j))
        while draft.words() < MIN_WORDS and f < len(order):
            draft.add("P", FILLERS[order[f]])
            f += 1
        text, note_labels = draft.render(note_id, p.id)
        author = clinic["nurse"] if kind.startswith("phone") else \
            clinic["clinician"].removeprefix("Dr. ") + ", MD"
        notes.append(Note(id=note_id, patient_id=p.id, date=e.date, author=author, text=text))
        labels.extend(note_labels)
    return notes, labels


# ---------------------------------------------------------------------------------------------
# Golden labels: every evidence-like sentence in the hand-written golden notes
# ---------------------------------------------------------------------------------------------

# (note_id, quote, category, qualifying, impairs, polarity, group)
GOLDEN_LABELS = [
    ("n-rosa-1", "History of type 2 diabetes on metformin", None, False, "unknown", "unstated", None),
    ("n-rosa-2", "Reports numbness and burning in both feet, worse at night, present for over a year.",
     "physical_disability", True, "unknown", "unstated", "diabetic_neuropathy"),
    ("n-rosa-2", "Type 2 diabetes mellitus with diabetic peripheral neuropathy",
     "physical_disability", True, "unknown", "unstated", "diabetic_neuropathy"),
    ("n-rosa-3", "her feet still feel numb most of the day",
     "physical_disability", True, "unknown", "unstated", "diabetic_neuropathy"),
    ("n-rosa-3", "Diabetic peripheral neuropathy on gabapentin",
     "physical_disability", True, "unknown", "unstated", "diabetic_neuropathy"),
    ("n-marcus-1", "History of COPD on tiotropium and albuterol",
     "serious_complex_medical", True, "unknown", "unstated", "copd"),
    ("n-marcus-1", "COPD, chronic, reported by patient as at baseline",
     "serious_complex_medical", True, "unknown", "unstated", "copd"),
    ("n-marcus-2", "Reports shortness of breath with minimal exertion such as dressing or showering.",
     "serious_complex_medical", True, "true", "positive", "copd"),
    ("n-marcus-2", "Cannot walk more than half a block without stopping to rest.",
     "serious_complex_medical", True, "true", "positive", "copd"),
    ("n-marcus-2", "Severe COPD, GOLD stage 3, with frequent exacerbations and exertional hypoxemia.",
     "serious_complex_medical", True, "unknown", "unstated", "copd"),
    ("n-marcus-3", "getting to the bus stop leaves him too winded",
     "serious_complex_medical", True, "true", "positive", "copd"),
    ("n-deshawn-1", "No numbness or tingling.", "physical_disability", False, "false", "negated", None),
    ("n-deshawn-2", "Wrist fully healed, playing basketball again without pain.",
     "physical_disability", False, "false", "resolved", None),
    ("n-linh-1", "Reports fatigue limiting her to about two hours of light activity before she must lie down.",
     "serious_complex_medical", True, "true", "positive", "ckd_advanced"),
    ("n-linh-1", "She stopped her job at a nail salon in the spring because she could not get through a shift.",
     "serious_complex_medical", True, "true", "positive", "ckd_advanced"),
    ("n-linh-1", "Chronic kidney disease stage 4, eGFR 22, secondary to hypertensive nephropathy.",
     "serious_complex_medical", True, "unknown", "unstated", "ckd_advanced"),
    ("n-linh-2", "Still tires quickly and needs to rest in the afternoon most days.",
     "serious_complex_medical", True, "true", "positive", "ckd_advanced"),
    ("n-linh-2", "Chronic kidney disease stage 4, declining, eGFR 19.",
     "serious_complex_medical", True, "unknown", "unstated", "ckd_advanced"),
    ("n-karen-1", "Working full time as a dental office manager.", None, False, "false", "function_normal", None),
    ("n-karen-1", "Major depressive disorder, recurrent, in sustained remission on sertraline 100 mg daily.",
     "serious_mental_illness", True, "false", "remission", "mental_illness"),
    ("n-karen-2", "Recently promoted at work and continues working full time.",
     None, False, "false", "function_normal", None),
    ("n-karen-2", "Major depressive disorder, recurrent, in remission.",
     "serious_mental_illness", True, "false", "remission", "mental_illness"),
    ("n-bea-1", "Patient's mother has severe arthritis", "physical_disability", False, "false", "family", None),
    ("n-bea-1", "Family history of arthritis, noted.", "physical_disability", False, "false", "family", None),
    ("n-bea-2", "Denies joint pain or difficulty walking.", "physical_disability", False, "false", "negated", None),
]


def golden_labels(notes: list[Note]) -> list[Label]:
    by_id = {n.id: n for n in notes}
    out = []
    for note_id, quote, category, qualifying, impairs, polarity, group in GOLDEN_LABELS:
        note = by_id[note_id]
        start, end = locate(note.text, quote)
        out.append(Label(note_id=note_id, patient_id=note.patient_id, start=start, end=end, category=category,
                         qualifying=qualifying, impairs=impairs, polarity=polarity, group=group,
                         library_id="golden", text=quote))
    return out


def check_labels(notes: list[Note], labels: list[Label]) -> None:
    """Every label's offsets land exactly on the sentence it records; raises on the first that does not."""
    by_id = {n.id: n for n in notes}
    for lb in labels:
        note = by_id.get(lb.note_id)
        if note is None or note.patient_id != lb.patient_id:
            raise ValueError(f"label points at a missing or foreign note: {lb}")
        if note.text[lb.start:lb.end] != lb.text:
            raise ValueError(f"label offsets do not match its text: {lb}")


def write_labels(labels: list[Label]) -> None:
    LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LABELS_PATH.open("w") as f:
        for lb in sorted(labels, key=lambda lb: (lb.note_id, lb.start)):
            f.write(lb.model_dump_json() + "\n")
