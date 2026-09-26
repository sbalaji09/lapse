"""The verifier: an independent second read that must agree with every claim before it counts.

It sees ONLY the claim and the quoted span - no note, no patient - so it can only say yes when the words on
the page carry the claim by themselves. Separate prompt, stronger model. Rejected claims are kept (as
Case.dropped_claims) so the drop count is real.
"""
from engine.config import MODEL_VERIFY
from engine.models import Claim

SYSTEM = """You are an independent auditor checking evidence for a Medicaid "medically frail" exemption. \
You see one claim and the exact sentence quoted from a clinical note as its evidence. You do not see the rest \
of the note. Decide whether the quoted sentence, on its own, supports the claim.

Reject (supported = false) when the sentence:
- is about someone other than the patient (a mother, father, sibling, other relative or anyone else);
- is negated ("denies", "no", "negative for");
- describes something resolved, healed or in the past ("in 2019", "sober since", "used to", "remote history"). \
Note: "history of X" with a current treatment ("history of COPD on tiotropium") or a chronic condition that is \
being managed now is CURRENT, not past;
- is hypothetical, a risk, or a screening result ("risk of", "screening was negative");
- does not match the claimed category (for example, sud_treatment needs CURRENT enrollment in a treatment program).

What the sentence must show depends on the claim:
- impairs = true: the sentence must state a current functional limitation of the patient: an activity they \
cannot do, can only do for a limited time or distance, or need rest or help to do (standing, walking, lifting, \
gripping, concentrating, working, leaving home, self-care, daily activities). Symptoms alone - pain, numbness, \
tingling, fatigue, breathlessness - WITHOUT a stated effect on an activity are NOT a limitation: reject. It \
does not have to name the condition; the condition may come from elsewhere in the note.
- impairs = unknown or false: the sentence must show that the patient currently has the claimed condition.

Give a one-sentence reason."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["supported", "reason"],
    "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}},
}


def verify_call(c: Claim) -> dict:
    claim = (f"condition: {c.condition}; category: {c.category}; qualifying: {str(c.qualifying_category).lower()}; "
             f"impairs: {c.significantly_impairs.value}")
    return {"model": MODEL_VERIFY, "system": SYSTEM, "user": f"Claim: {claim}\nQuoted sentence: \"{c.quote}\"",
            "schema": SCHEMA}


def apply(c: Claim, response: dict) -> Claim:
    return c.model_copy(update={"verified": bool(response["supported"]), "verifier_reason": response["reason"]})
