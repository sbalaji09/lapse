"""Channel B: the Evidence Finder. An LLM reads each note and returns impairment claims that point at exact text.

"We would rather miss someone than invent proof": every claim carries a verbatim quote, the offsets are found
in code (never asked of the model), a claim whose quote cannot be found is dropped as unlocatable, and an
independent verifier (engine/verifier.py) must agree with each claim before it counts.

Division of labour: extraction is tuned for recall (propose anything that might be evidence, even a relative's
condition or a past problem); the verifier, seeing only the quoted sentence, is tuned for precision. That makes
the verifier's drop count a real measurement, not a formality.

Channel B only ever ADDS positive facts (qualifying_condition, significantly_impairs, in_sud_treatment = true)
on top of what the state already knows. Absence of evidence in a note is not evidence of absence.
"""
import re

from engine.config import MODEL_FAST, PIPELINE_RUN_AT
from engine.models import Claim, Determination, Fact, Note, Source, Tri
from engine.rulepack import RulePack

CATEGORIES = ["physical_disability", "serious_complex_medical", "serious_mental_illness", "sud",
              "intellectual_developmental_disability", "blind", "sud_treatment"]

SYSTEM = """You read one clinical progress note and extract evidence relevant to a Medicaid "medically frail" \
exemption. You return claims; each claim quotes the note exactly.

Qualifying categories (qualifying_category = true):
- physical_disability: neuropathy, arthritis, chronic pain conditions, back or neck pain syndromes, \
fibromyalgia, neurological disability (brain injury, cerebral palsy, stroke, dementia), spasticity.
- serious_complex_medical: chronic kidney disease or dialysis, COPD, heart failure or coronary disease, cancer \
under treatment, HIV, asthma, chronic migraine, seizure disorder or epilepsy.
- serious_mental_illness: major depression, anxiety or panic disorder, bipolar disorder, schizophrenia.
- sud: a current substance use disorder (opioid, alcohol, other drugs).
- intellectual_developmental_disability.
- blind.
Not qualifying on their own: diabetes, hypertension, high cholesterol, prediabetes, obesity, anemia, acute \
infections, injuries. Diabetes WITH a complication (such as neuropathy or kidney disease) qualifies through \
the complication.

Also extract, as category "sud_treatment" with qualifying_category = false, any statement that the patient is \
CURRENTLY enrolled in a substance use treatment program.

Rules:
1. quote is copied character for character from the note: one sentence or one clause. Never paraphrase, \
never join text from two places.
2. Do not miss anything: you are the first pass, and a separate auditor checks every claim against its \
sentence and removes the ones that are not about the patient's current situation. So propose EVERY sentence \
that names a qualifying condition or describes a functional limitation, including:
   - every diagnosis line in the assessment (for example "Severe COPD, GOLD stage 3, ...");
   - conditions in remission or well controlled (set significantly_impairs to "false" if the text says so);
   - conditions of relatives ("mother has severe arthritis") - fill in the claim as if it were the patient's;
   - past, resolved, negated or hypothetical mentions.
   Do not judge whether they apply to the patient; that is the auditor's job.
3. significantly_impairs: "true" only if the quoted text itself states a functional limitation - standing, \
walking, lifting, gripping, concentrating, working, leaving home, self-care or daily activities. "unknown" \
when the condition is present but the quoted text says nothing about function. "false" when the quoted text \
says the condition does not limit the patient (for example, in remission and working full time).
4. A sentence that states a limitation without naming the condition is still a claim: name the condition from \
the rest of the note in `condition`, and quote the limitation sentence.
5. condition is a short plain name, for example "diabetic peripheral neuropathy" or "severe COPD".
6. If nothing qualifies, return an empty list."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "condition", "qualifying_category", "significantly_impairs", "quote"],
                "properties": {
                    "category": {"type": "string", "enum": CATEGORIES},
                    "condition": {"type": "string"},
                    "qualifying_category": {"type": "boolean"},
                    "significantly_impairs": {"type": "string", "enum": ["true", "false", "unknown"]},
                    "quote": {"type": "string"},
                },
            },
        },
    },
}


def extraction_call(note: Note) -> dict:
    return {"model": MODEL_FAST, "system": SYSTEM, "user": note.text, "schema": SCHEMA}


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """Offsets of quote in text: exact first, then whitespace-normalized. None = unlocatable."""
    quote = quote.strip()
    if not quote:
        return None
    start = text.find(quote)
    if start != -1:
        return start, start + len(quote)
    # Whitespace-normalized match, mapped back to offsets in the original text.
    norm_chars, index = [], []
    for i, ch in enumerate(text):
        if ch.isspace():
            if norm_chars and norm_chars[-1] == " ":
                continue
            norm_chars.append(" ")
        else:
            norm_chars.append(ch)
        index.append(i)
    norm = "".join(norm_chars)
    q = re.sub(r"\s+", " ", quote)
    at = norm.find(q)
    if at == -1:
        return None
    return index[at], index[at + len(q) - 1] + 1


def to_claims(note: Note, response: dict) -> tuple[list[Claim], int]:
    """Model output -> Claims with offsets computed here. Returns (claims, unlocatable count)."""
    claims, unlocatable, seen = [], 0, set()
    for raw in response["claims"]:
        span = locate(note.text, raw["quote"])
        if span is None:
            unlocatable += 1
            continue
        start, end = span
        if (start, end, raw["category"]) in seen:
            continue
        seen.add((start, end, raw["category"]))
        claims.append(Claim(
            id=f"c-{note.id.removeprefix('n-')}-{len(claims) + 1}", patient_id=note.patient_id, note_id=note.id,
            category=raw["category"], condition=raw["condition"], qualifying_category=raw["qualifying_category"],
            significantly_impairs=Tri(raw["significantly_impairs"]),
            quote=note.text[start:end], start=start, end=end,
        ))
    return claims, unlocatable


def _span_fact(key: str, c: Claim, pack: RulePack) -> Fact:
    return Fact(id=f"fb-{c.patient_id}-{key}", patient_id=c.patient_id, key=key, value=True, source=Source.note_span,
                source_ref={"note_id": c.note_id, "start": c.start, "end": c.end, "claim_id": c.id}, quote=c.quote,
                recorded_at=PIPELINE_RUN_AT, rule_pack_version=pack.version)


def evidence_facts(claims: list[Claim], pack: RulePack) -> list[Fact]:
    """Positive facts from VERIFIED claims only, each pointing at its best supporting span."""
    ok = [c for c in claims if c.verified]
    qualifying = [c for c in ok if c.qualifying_category and c.category != "sud_treatment"]
    facts = []
    if qualifying:
        # Prefer a span that names the condition (impairs unknown) for the qualifying test...
        facts.append(_span_fact("qualifying_condition", sorted(qualifying, key=lambda c: c.significantly_impairs != Tri.unknown)[0], pack))
        # ...and a span that states the limitation for the impairment test.
        impairing = [c for c in qualifying if c.significantly_impairs == Tri.true]
        if impairing:
            facts.append(_span_fact("significantly_impairs", impairing[0], pack))
    treatment = [c for c in ok if c.category == "sud_treatment"]
    if treatment:
        facts.append(_span_fact("in_sud_treatment", treatment[0], pack))
    return facts


def determine(patient_id: str, a_facts: list[Fact], b_facts: list[Fact], pack: RulePack) -> Determination:
    """Channel B's determination: what the state knows plus what the notes prove."""
    values = {f.key: f.value for f in a_facts}
    values.update({f.key: f.value for f in b_facts})
    status, rule_ids = pack.determine(values)
    return Determination(patient_id=patient_id, channel="B", status=status, rule_ids=rule_ids,
                         fact_ids=[f.id for f in a_facts] + [f.id for f in b_facts], rule_pack_version=pack.version)
