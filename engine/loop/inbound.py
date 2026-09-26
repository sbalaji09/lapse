"""Inbound patient-reply parsing. Deterministic keyword parser, not an LLM call.

# ponytail: keyword parser, not an LLM call — no API key required for this demo build;
# upgrade to engine.llm.call_json when there's key + budget for open-ended replies
"""
import re
from datetime import datetime, timezone

from engine.models import CaseStatus, Fact, Holder, Source
import engine.config as config
import engine.store as store

_NUMBER_WORDS = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
# ponytail: digits + a small es/en number-word table, not a full numeral parser;
# extend the table (or swap to a numeral-parsing lib) if replies use bigger/compound numbers.
_NUMBER_TOKEN = r"(?:\d+|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")"
_MINUTE_PATTERNS = [
    re.compile(_NUMBER_TOKEN + r"\s*minuto[s]?", re.IGNORECASE),
    re.compile(_NUMBER_TOKEN + r"\s*minute[s]?", re.IGNORECASE),
]
_NUMBER_RE = re.compile(r"\d+")

_STOPPED_WORKING_RE = re.compile(r"dej[eé] de trabajar|stopped working", re.IGNORECASE)


def parse_reply(missing_fact, text: str) -> dict | None:
    if missing_fact.key != "standing_tolerance_minutes":
        return None
    for pattern in _MINUTE_PATTERNS:
        match = pattern.search(text)
        if match:
            quote = match.group(0)
            digit_match = _NUMBER_RE.search(quote)
            if digit_match:
                value = int(digit_match.group(0))
            else:
                word = quote.split()[0].lower()
                value = _NUMBER_WORDS[word]
            return {"key": missing_fact.key, "value": value, "quote": quote}
    return None


def parse_side_facts(text: str) -> list[dict]:
    match = _STOPPED_WORKING_RE.search(text)
    if match:
        return [{"key": "hours_per_month", "value": 0, "quote": match.group(0)}]
    return []


def handle_reply(case_id: str, text: str, message_id: str | None = None) -> dict:
    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"no such case: {case_id}")

    now = datetime.now(timezone.utc).isoformat()
    case.events.append({
        "at": now,
        "kind": "patient_reply_received",
        "detail": {"message_id": message_id, "text": text},
    })

    open_patient_facts = [
        m for m in case.missing if m.holder == Holder.patient and m.status in ("open", "asked")
    ]
    if not open_patient_facts:
        store.save_case(case)
        return {"status": case.status.value, "parsed": False}

    target = open_patient_facts[0]
    parsed = parse_reply(target, text)
    if parsed is None:
        case.events.append({
            "at": now,
            "kind": "reply_needs_human_read",
            "detail": {"message_id": message_id, "text": text, "missing_fact_id": target.id},
        })
        store.save_case(case)
        return {"status": case.status.value, "parsed": False}

    parsed_items = [parsed] + parse_side_facts(text)
    for item in parsed_items:
        fact = Fact(
            id=f"fact-{case_id}-{item['key']}-{len(case.facts)}",
            patient_id=case_id,
            key=item["key"],
            value=item["value"],
            source=Source.patient_reply,
            source_ref={"message_id": message_id} if message_id else {},
            quote=item["quote"],
            recorded_at=datetime.now(timezone.utc),
            rule_pack_version=config.ACTIVE_RULE_PACK,
        )
        case.facts.append(fact)

    target.status = "resolved_true"

    # ponytail: local re-determination shortcut, not the full A4 solver re-run;
    # import engine.solver here and re-run it once it exists.
    remaining = [m for m in case.missing if m.status == "open" and m.id != target.id]
    if remaining and remaining[0].holder == Holder.clinician:
        case.status = CaseStatus.waiting_clinician
    elif remaining:
        case.status = CaseStatus.waiting_patient
    else:
        case.status = CaseStatus.needs_action

    store.save_case(case)
    return {"status": case.status.value, "parsed": True, "fact_key": target.key}
