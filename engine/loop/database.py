"""Operator clicks "Check <database>": answer the case's open database-held fact by lookup. Nobody is contacted."""
from datetime import datetime, timezone

import engine.solver as solver
import engine.store as store
from engine.loop.workflow import status_after_reevaluation
from engine.models import Holder


def check_database(case_id: str) -> dict:
    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"no such case: {case_id}")
    target = next(
        (m for m in case.missing if m.holder == Holder.database and m.status in ("open", "asked")),
        None,
    )
    if target is None:
        raise ValueError(f"{case_id} has no open fact a database can answer")

    before = case.bucket
    fact = solver.resolve_database(case, target, at=datetime.now(timezone.utc))
    case.facts.append(fact)
    target.status = "resolved_true" if fact.value else "resolved_false"
    case = solver.reevaluate(case)
    case.status = status_after_reevaluation(case, fallback=solver.default_status(case))
    label = solver.DB_LABELS.get(target.database, target.database)
    outcome = " Resolved without contacting anyone." if case.bucket.value == "SAFE" else " Nobody contacted."
    detail = (
        f"Checked {label}: {'confirmed' if fact.value else 'no record'}."
        + outcome
    )
    case.events.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": "database_checked",
        "detail": {
            "database": target.database,
            "fact": target.key,
            "value": fact.value,
            "bucket_before": before.value,
            "bucket_after": case.bucket.value,
            "text": detail,
        },
    })
    store.save_case(case)
    return {
        "status": case.status.value,
        "bucket": case.bucket.value,
        "fact_key": target.key,
        "value": fact.value,
    }
