"""Stream a live Channel B run over the cases currently visible in the product."""
import asyncio
import collections
import json
import threading
import time
import uuid
from datetime import datetime, timezone

from engine import buckets, channel_b, llm, solver, store, verifier
from engine.models import Case, Source
from engine.rulepack import load_pack


# Leave this lookup open so the demo can show the operator resolving it.
DEMO_DATABASE_CLICK = ("g-deshawn", "enrolled_half_time_school")
PATIENT_CONCURRENCY = 4

_changed = threading.Condition()
_job: dict | None = None


def _public_job(include_events: bool = False) -> dict:
    if _job is None:
        return {"status": "idle"}
    result = {key: value for key, value in _job.items() if key not in {"events", "started_monotonic"}}
    if include_events:
        result["events"] = list(_job["events"])
    return result


def _publish(event: dict) -> None:
    with _changed:
        if _job is None:
            return
        event = {"sequence": len(_job["events"]), **event}
        _job["events"].append(event)
        patient_id = event.get("patient_id")
        if patient_id:
            state = {"status": event["status"]}
            if "result" in event:
                state["result"] = event["result"]
            if "error" in event:
                state["error"] = event["error"]
            _job["patient_states"][patient_id] = state
        _changed.notify_all()


def _state_only(case: Case) -> Case:
    """Remove prior note evidence before the live read so pending charts are genuinely untouched."""
    state_fact_ids = set(case.determination_a.fact_ids)
    case = case.model_copy(deep=True)
    case.claims = []
    case.dropped_claims = []
    case.facts = [fact for fact in case.facts if fact.id in state_fact_ids]
    case.missing = []
    case.determination_final = buckets.final_determination(case.patient_id, case.facts, load_pack())
    case = solver.reevaluate(case)
    case.fragile = buckets.is_fragile(case.determination_a, [])
    case.status = solver.default_status(case)
    return case


def _result(case: Case) -> dict:
    top_missing = next((item for item in case.missing if item.status in ("open", "asked")), None)
    return {
        "bucket": case.bucket.value,
        "fragile": case.fragile,
        "case_status": case.status.value,
        "verified_spans": len(case.claims),
        "top_missing_fact": {
            "key": top_missing.key,
            "holder": top_missing.holder.value,
            "database": top_missing.database,
            "why": top_missing.why,
        } if top_missing else None,
    }


async def _read_patient(case: Case, notes) -> dict:
    extraction_responses = await llm.run_batch_async(
        [channel_b.extraction_call(note) for note in notes],
        use_cache=False,
    )

    claims = []
    unlocatable = 0
    for note, response in zip(notes, extraction_responses):
        found, missing = channel_b.to_claims(note, response)
        claims.extend(found)
        unlocatable += missing

    if claims:
        verdicts = await llm.run_batch_async(
            [verifier.verify_call(claim) for claim in claims],
            use_cache=False,
        )
        claims = [verifier.apply(claim, verdict) for claim, verdict in zip(claims, verdicts)]

    pack = load_pack()
    case.claims = [claim for claim in claims if claim.verified]
    case.dropped_claims = [claim for claim in claims if claim.verified is False]
    case.facts.extend(channel_b.evidence_facts(claims, pack))
    case.determination_final = buckets.final_determination(case.patient_id, case.facts, pack)
    keep_open = (DEMO_DATABASE_CLICK[1],) if case.patient_id == DEMO_DATABASE_CLICK[0] else ()
    case, _ = solver.settle(case, pack, keep_open=keep_open, at=datetime.now(timezone.utc))
    case.fragile = buckets.is_fragile(case.determination_a, claims)
    case.status = solver.default_status(case)

    store.replace_claims({case.patient_id}, claims)
    store.save_case(case)
    kept = sum(bool(claim.verified) for claim in claims)
    return {
        **_result(case),
        "notes": len(notes),
        "claims": len(claims) + unlocatable,
        "kept": kept,
        "dropped": len(claims) - kept,
        "unlocatable": unlocatable,
    }


async def _run(cases: list[Case], notes_by_patient: dict) -> None:
    semaphore = asyncio.Semaphore(PATIENT_CONCURRENCY)

    async def one(case: Case) -> None:
        async with semaphore:
            _publish({"type": "patient", "patient_id": case.patient_id, "status": "reading"})
            try:
                result = await _read_patient(case, notes_by_patient[case.patient_id])
            except Exception as error:
                _publish({
                    "type": "patient",
                    "patient_id": case.patient_id,
                    "status": "failed",
                    "error": str(error),
                })
                return
            _publish({
                "type": "patient",
                "patient_id": case.patient_id,
                "status": "complete",
                "result": result,
            })

    await asyncio.gather(*(one(case) for case in cases))
    with _changed:
        if _job is None:
            return
        states = _job["patient_states"].values()
        _job["completed"] = sum(state["status"] == "complete" for state in states)
        _job["failed"] = sum(state["status"] == "failed" for state in states)
        complete_results = [state["result"] for state in states if state["status"] == "complete"]
        _job["claims"] = sum(result["claims"] for result in complete_results)
        _job["kept"] = sum(result["kept"] for result in complete_results)
        _job["dropped"] = sum(result["dropped"] for result in complete_results)
        _job["unlocatable"] = sum(result["unlocatable"] for result in complete_results)
        _job["status"] = "complete" if _job["failed"] == 0 else "complete_with_errors"
        _job["elapsed_seconds"] = round(time.monotonic() - _job["started_monotonic"], 1)
    _publish({
        "type": "run",
        "status": _job["status"],
        "completed": _job["completed"],
        "failed": _job["failed"],
        "elapsed_seconds": _job["elapsed_seconds"],
        "claims": _job["claims"],
        "kept": _job["kept"],
        "dropped": _job["dropped"],
        "unlocatable": _job["unlocatable"],
    })


def _worker(cases: list[Case], notes_by_patient: dict) -> None:
    try:
        asyncio.run(_run(cases, notes_by_patient))
    except Exception as error:
        with _changed:
            if _job is not None:
                _job["status"] = "failed"
                _job["error"] = str(error)
        _publish({"type": "run", "status": "failed", "error": str(error)})


def start() -> dict:
    global _job
    with _changed:
        if _job is not None and _job["status"] == "running":
            return _public_job()

    cases = store.list_cases()
    if not cases:
        raise RuntimeError("no cases are loaded")

    patient_ids = {case.patient_id for case in cases}
    notes = [note for note in store.all_notes() if note.patient_id in patient_ids]
    if not notes:
        raise RuntimeError("the loaded cases have no clinical notes")

    notes_by_patient = collections.defaultdict(list)
    for note in notes:
        notes_by_patient[note.patient_id].append(note)

    prepared = [_state_only(case) for case in cases]
    store.replace_claims(patient_ids, [])
    store.replace_cases(prepared)

    started = time.monotonic()
    _job = {
        "run_id": uuid.uuid4().hex,
        "status": "running",
        "provider": llm.provider(),
        "patients": len(cases),
        "notes": len(notes),
        "completed": 0,
        "failed": 0,
        "elapsed_seconds": 0,
        "patient_states": {case.patient_id: {"status": "pending"} for case in cases},
        "events": [],
        "started_monotonic": started,
    }
    _publish({
        "type": "run",
        "status": "running",
        "provider": _job["provider"],
        "patients": len(cases),
        "notes": len(notes),
    })
    threading.Thread(target=_worker, args=(prepared, notes_by_patient), daemon=True).start()
    return _public_job()


def status() -> dict:
    with _changed:
        return _public_job()


def event_stream(run_id: str):
    index = 0
    while True:
        with _changed:
            _changed.wait_for(
                lambda: _job is None
                or _job["run_id"] != run_id
                or len(_job["events"]) > index
                or _job["status"] != "running",
                timeout=10,
            )
            if _job is None or _job["run_id"] != run_id:
                events = [{"type": "run", "status": "missing"}]
                terminal = True
            else:
                events = list(_job["events"][index:])
                terminal = _job["status"] != "running"
        if not events:
            yield ": keepalive\n\n"
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"
            index = event.get("sequence", index) + 1
        with _changed:
            caught_up = _job is None or _job["run_id"] != run_id or index >= len(_job["events"])
        if terminal and caught_up:
            return
