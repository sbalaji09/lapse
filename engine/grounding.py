"""Compare Bedrock Contextual Grounding with the existing claim verifier.

    python -m engine.grounding     # writes data/grounding.json
"""
import json
from datetime import datetime, timezone

from engine import guardrails, store
from engine.config import DATA_DIR
from engine.models import Claim, Tri


GROUNDING_PATH = DATA_DIR / "grounding.json"
QUERY = """Using only the clinical note, decide whether the response is supported as a current fact about the
patient and is relevant clinical evidence. A relative's condition, negated finding, resolved or remote history,
hypothetical, risk, or negative screening does not support a claim about the patient. Symptoms alone do not
support a functional-limitation assertion unless the note states an effect on work or a daily activity."""


def assertion_for(claim: Claim) -> str:
    if claim.category == "sud_treatment":
        return "The patient is currently enrolled in treatment for a substance use disorder."
    assertion = f"The patient currently has {claim.condition}."
    if claim.significantly_impairs == Tri.true:
        assertion += " The patient currently has a functional limitation affecting work or a daily activity."
    elif claim.significantly_impairs == Tri.false:
        assertion += " The note says this condition does not currently cause a functional limitation."
    return assertion


def current_claims() -> list[Claim]:
    """Final cases contain both verifier-kept and verifier-dropped claims."""
    claims = {}
    for case in store.list_cases():
        for claim in case.claims + case.dropped_claims:
            claims[claim.id] = claim
    if not claims:
        # Also works immediately after Channel B, before final cases are assembled.
        claims = {claim.id: claim for claim in store.list_claims()}
    return sorted(claims.values(), key=lambda claim: (claim.patient_id, claim.note_id, claim.id))


def summarize(rows: list[dict]) -> dict:
    both_kept = sum(row["verifier_kept"] and row["grounding_kept"] for row in rows)
    both_dropped = sum(not row["verifier_kept"] and not row["grounding_kept"] for row in rows)
    verifier_only = sum(row["verifier_kept"] and not row["grounding_kept"] for row in rows)
    grounding_only = sum(not row["verifier_kept"] and row["grounding_kept"] for row in rows)
    agreed = both_kept + both_dropped
    return {
        "claims": len(rows),
        "both_kept": both_kept,
        "both_dropped": both_dropped,
        "verifier_kept_grounding_dropped": verifier_only,
        "verifier_dropped_grounding_kept": grounding_only,
        "agreement": round(agreed / len(rows), 3) if rows else 0.0,
    }


def run() -> dict:
    claims = current_claims()
    if not claims:
        raise RuntimeError("no Channel B claims found; load fixtures or run the Channel B pipeline")
    notes = {note.id: note for note in store.all_notes()}
    rows = []
    for claim in claims:
        note = notes.get(claim.note_id)
        if note is None:
            raise RuntimeError(f"claim {claim.id} references missing note {claim.note_id}")
        assertion = assertion_for(claim)
        assessment = guardrails.check_grounding(note.text, QUERY, assertion)
        rows.append({
            "claim_id": claim.id,
            "patient_id": claim.patient_id,
            "note_id": claim.note_id,
            "quote": claim.quote,
            "assertion": assertion,
            "verifier_kept": claim.verified is True,
            "verifier_reason": claim.verifier_reason,
            "grounding_kept": assessment["kept"],
            "grounding_score": assessment["grounding"]["score"],
            "grounding_threshold": assessment["grounding"]["threshold"],
            "relevance_score": assessment["relevance"]["score"],
            "relevance_threshold": assessment["relevance"]["threshold"],
        })
    report = {
        "label": "Bedrock Contextual Grounding compared with the Lapse verifier on synthetic notes.",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "guardrail": guardrails.identity(),
        "summary": summarize(rows),
        "claims": rows,
    }
    GROUNDING_PATH.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    report = run()
    summary = report["summary"]
    print(f"grounding: {summary['claims']} claims, {summary['agreement']:.1%} agreement")
    print(f"  both kept: {summary['both_kept']}; both dropped: {summary['both_dropped']}")
    print(
        "  disagreements: "
        f"verifier only {summary['verifier_kept_grounding_dropped']}; "
        f"grounding only {summary['verifier_dropped_grounding_kept']}"
    )
    print(f"wrote {GROUNDING_PATH}")


if __name__ == "__main__":
    main()
