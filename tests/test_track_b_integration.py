import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import httpx

from api.main import app
from engine import solver
from engine import store
from engine.models import Fact, Source
from engine.loop.clinician import clinician_token


ROSA_REPLY = "Dejé de trabajar en marzo, la espalda no me aguanta más de diez minutos de pie."


def api_requests(*steps):
    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = []
            for method, path, *options in steps:
                responses.append(await client.request(method, path, **(options[0] if options else {})))
            return tuple(responses)

    return asyncio.run(request())


def test_demo_reset_summary_and_queue_serve_seven_fixtures(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))

    reset, summary, queue, rulepack = api_requests(
        ("POST", "/api/demo/reset"),
        ("GET", "/api/summary"),
        ("GET", "/api/queue"),
        ("POST", "/api/rulepack/ne"),
    )

    assert reset.status_code == 200
    assert reset.json() == {"reloaded": 7}
    assert summary.status_code == 200
    assert summary.json()["cohort"] == 7
    assert summary.json()["recovered"] == 1
    assert summary.json()["rule_pack"] == "ca-2027.02-demo"
    assert queue.status_code == 200
    assert [case["id"] for case in queue.json()] == [
        "g-marcus",
        "g-rosa",
        "g-deshawn",
        "g-bea",
        "g-linh",
        "g-karen",
        "g-omar",
    ]
    assert rulepack.status_code == 501
    assert rulepack.json()["detail"] == "not implemented"


def test_eval_serves_committed_artifact():
    response, = api_requests(("GET", "/api/eval"))
    expected = json.loads((Path(__file__).parents[1] / "data" / "eval.json").read_text())

    assert response.status_code == 200
    assert response.json() == expected


def test_deshawn_database_lookup_reevaluates_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))

    reset, lookup, case = api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-deshawn/check-database"),
        ("GET", "/api/cases/g-deshawn"),
    )

    assert reset.status_code == 200
    assert reset.json() == {"reloaded": 7}
    assert lookup.status_code == 200
    assert case.status_code == 200

    result = lookup.json()
    persisted = case.json()
    assert result == {
        "status": "no_action",
        "bucket": "SAFE",
        "fact_key": "enrolled_half_time_school",
        "value": True,
    }
    assert persisted["bucket"] == "SAFE"
    assert persisted["status"] == "no_action"
    assert persisted["determination_final"]["status"] == "compliant"
    assert persisted["determination_final"]["rule_ids"] == ["school"]

    school_fact = next(
        fact for fact in persisted["facts"]
        if fact["key"] == "enrolled_half_time_school"
    )
    assert school_fact["value"] is True
    assert school_fact["source"] == "external_db"
    assert school_fact["source_ref"] == {
        "db": "student_enrollment",
        "record_id": "se-deshawn",
    }

    missing = next(
        item for item in persisted["missing"]
        if item["key"] == "enrolled_half_time_school"
    )
    assert missing["status"] == "resolved_true"

    database_events = [
        event for event in persisted["events"]
        if event["kind"] == "database_checked"
    ]
    assert len(database_events) == 1
    assert database_events[0]["detail"] == {
        "database": "student_enrollment",
        "fact": "enrolled_half_time_school",
        "value": True,
        "bucket_before": "ONE_AWAY",
        "bucket_after": "SAFE",
        "text": "Checked the student enrollment database: confirmed. Resolved without contacting anyone.",
    }
    assert not any(
        event["kind"] in {"email_sent", "patient_replied", "clinician_signed", "clinician_declined"}
        for event in persisted["events"]
    )


def test_database_lookup_records_truthful_no_record_event(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))
    real_resolve_database = solver.resolve_database

    def no_record(case, missing, at=None):
        fact = real_resolve_database(case, missing, at=at)
        return fact.model_copy(update={
            "value": False,
            "source_ref": {"db": missing.database, "record_id": None},
    })

    monkeypatch.setattr(solver, "resolve_database", no_record)

    _, lookup, case = api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-deshawn/check-database"),
        ("GET", "/api/cases/g-deshawn"),
    )

    assert lookup.status_code == 200
    assert case.status_code == 200
    persisted = case.json()
    fact = next(
        fact for fact in persisted["facts"]
        if fact["key"] == "enrolled_half_time_school" and fact["source"] == "external_db"
    )
    missing = next(
        item for item in persisted["missing"]
        if item["key"] == "enrolled_half_time_school"
    )
    assert fact["value"] is False
    assert fact["source_ref"] == {"db": "student_enrollment", "record_id": None}
    assert missing["status"] == "resolved_false"
    assert persisted["determination_final"]["status"] == "not_determined"
    assert persisted["bucket"] == "ONE_AWAY"
    assert persisted["status"] == "needs_action"
    assert lookup.json() == {
        "status": "needs_action",
        "bucket": "ONE_AWAY",
        "fact_key": "enrolled_half_time_school",
        "value": False,
    }
    event = next(event for event in case.json()["events"] if event["kind"] == "database_checked")
    assert event["detail"] == {
        "database": "student_enrollment",
        "fact": "enrolled_half_time_school",
        "value": False,
        "bucket_before": "ONE_AWAY",
        "bucket_after": "ONE_AWAY",
        "text": "Checked the student enrollment database: no record. Nobody contacted.",
    }


def test_database_lookup_rejects_repeat_call(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))

    _, _, response = api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-deshawn/check-database"),
        ("POST", "/api/cases/g-deshawn/check-database"),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "g-deshawn has no open fact a database can answer"


def test_database_lookup_rejects_invalid_case(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))

    response, = api_requests(("POST", "/api/cases/g-missing/check-database"))

    assert response.status_code == 400
    assert response.json()["detail"] == "no such case: g-missing"


def test_rosa_reply_reevaluates_to_provable(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))

    reset, ask, reply, case = api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-rosa/ask"),
        ("POST", "/api/cases/g-rosa/reply", {"json": {"text": ROSA_REPLY}}),
        ("GET", "/api/cases/g-rosa"),
    )

    assert reset.status_code == 200
    assert ask.status_code == 200
    assert reply.status_code == 200
    assert reply.json()["parsed"] is True
    assert case.status_code == 200

    persisted = case.json()
    patient_fact = next(
        fact for fact in persisted["facts"]
        if fact["key"] == "standing_tolerance_minutes" and fact["source"] == "patient_reply"
    )
    assert patient_fact["quote"] == "diez minutos"
    received = next(
        event for event in persisted["events"]
        if event["kind"] == "patient_reply_received"
    )
    message_id = received["detail"]["message_id"]
    assert isinstance(message_id, str)
    assert message_id == "reply-g-rosa-1"
    patient_facts = [
        fact for fact in persisted["facts"]
        if fact["source"] == "patient_reply"
    ]
    assert len(patient_facts) == 2
    assert {
        fact["source_ref"]["message_id"] for fact in patient_facts
    } == {message_id}
    assert {
        fact["rule_pack_version"] for fact in patient_facts
    } == {persisted["determination_final"]["rule_pack_version"]}

    standing = next(
        item for item in persisted["missing"]
        if item["key"] == "standing_tolerance_minutes"
    )
    assert standing["status"] == "resolved_true"
    assert persisted["bucket"] == "PROVABLE"
    assert persisted["determination_final"]["status"] == "exempt"
    assert persisted["determination_final"]["rule_ids"] == ["medically_frail"]
    assert [
        item["key"] for item in persisted["missing"]
        if item["status"] == "open"
    ] == ["limitation_attested"]
    assert persisted["status"] == "waiting_clinician"
    assert persisted["clinician_url"] == f"/clinician/{clinician_token('g-rosa')}"


def test_long_standing_tolerance_is_resolved_false(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))

    _, reply, case = api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-rosa/reply", {"json": {"text": "Puedo estar de pie 120 minutos."}}),
        ("GET", "/api/cases/g-rosa"),
    )

    assert reply.status_code == 200
    assert reply.json()["bucket"] == "ONE_AWAY"
    persisted = case.json()
    standing = next(
        item for item in persisted["missing"]
        if item["key"] == "standing_tolerance_minutes"
    )
    assert standing["status"] == "resolved_false"
    assert persisted["determination_final"]["status"] == "not_determined"


def test_clinician_sign_reruns_solver_and_finishes_attestation(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))
    token = clinician_token("g-rosa")

    reset, reply, sign, case = api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-rosa/reply", {"json": {"text": ROSA_REPLY}}),
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "sign"}}),
        ("GET", "/api/cases/g-rosa"),
    )

    assert reset.status_code == 200
    assert reply.status_code == 200
    assert sign.status_code == 200
    assert case.status_code == 200

    persisted = case.json()
    assert persisted["status"] == "attestation_ready"
    assert persisted["determination_final"]["status"] == "exempt"
    assert persisted["determination_final"]["rule_ids"] == ["medically_frail"]

    attestation = next(
        fact for fact in persisted["facts"]
        if fact["key"] == "limitation_attested" and fact["source"] == "clinician_attestation"
    )
    assert attestation["value"] is True
    assert attestation["source_ref"] == {"attestation_id": "att-g-rosa"}
    assert attestation["rule_pack_version"] == persisted["determination_final"]["rule_pack_version"]

    limitation = next(
        item for item in persisted["missing"]
        if item["key"] == "limitation_attested"
    )
    assert limitation["status"] == "resolved_true"
    assert not any(
        item["holder"] == "clinician" and item["status"] == "open"
        for item in persisted["missing"]
    )
    assert any(event["kind"] == "clinician_signed" for event in persisted["events"])


REPO_ROOT = Path(__file__).parents[1]


def test_make_api_uses_configurable_python_from_repository_root():
    default = subprocess.run(
        ["make", "-n", "api"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    result = subprocess.run(
        ["make", "-n", "PYTHON=custom-python", "api"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert default.returncode == 0
    assert "python3 -m uvicorn api.main:app --reload --port 8000" in default.stdout
    assert result.returncode == 0
    assert result.stderr == ""
    assert "custom-python -m uvicorn api.main:app --reload --port 8000" in result.stdout
    assert "cd api" not in result.stdout
    assert "--reload" in result.stdout


def test_make_api_app_imports_with_uvicorn_importer():
    result = subprocess.run(
        [
            "python3",
            "-c",
            "from uvicorn.importer import import_from_string; import_from_string('api.main:app')",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_make_pipeline_uses_configurable_python_and_forwards_args():
    result = subprocess.run(
        ["make", "-n", "PYTHON=custom-python", "pipeline", "ARGS=--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert "custom-python -m engine.pipeline --help" in result.stdout


def test_make_pipeline_help_runs_from_repository_root():
    result = subprocess.run(
        ["make", "pipeline", "ARGS=--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage" in result.stdout.lower() or "help" in result.stdout.lower()


def test_clinician_sign_without_reply_rejects_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))
    token = clinician_token("g-rosa")

    _, before, sign, after = api_requests(
        ("POST", "/api/demo/reset"),
        ("GET", "/api/cases/g-rosa"),
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "sign"}}),
        ("GET", "/api/cases/g-rosa"),
    )

    assert sign.status_code == 400
    assert before.json()["status"] == after.json()["status"]
    assert before.json()["bucket"] == after.json()["bucket"]
    assert before.json()["determination_final"] == after.json()["determination_final"]
    assert not any(
        fact["source"] == "clinician_attestation"
        for fact in after.json()["facts"]
    )
    assert not any(
        event["kind"].startswith("clinician_")
        for event in after.json()["events"]
    )


def test_second_clinician_sign_rejects_without_duplicate_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))
    token = clinician_token("g-rosa")

    _, _, first, second, case = api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-rosa/reply", {"json": {"text": ROSA_REPLY}}),
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "sign"}}),
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "sign"}}),
        ("GET", "/api/cases/g-rosa"),
    )

    assert first.status_code == 200
    assert second.status_code == 400
    persisted = case.json()
    assert sum(
        fact["source"] == "clinician_attestation"
        for fact in persisted["facts"]
    ) == 1
    assert sum(
        event["kind"] == "clinician_signed"
        for event in persisted["events"]
    ) == 1


def test_clinician_decline_closes_item_and_rejects_repeats(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))
    token = clinician_token("g-rosa")

    _, _, decline, second_decline, sign, case = api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-rosa/reply", {"json": {"text": ROSA_REPLY}}),
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "decline"}}),
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "decline"}}),
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "sign"}}),
        ("GET", "/api/cases/g-rosa"),
    )

    assert decline.status_code == 200
    assert second_decline.status_code == 400
    assert sign.status_code == 400
    persisted = case.json()
    assert persisted["status"] == "needs_action"
    limitation = next(
        item for item in persisted["missing"]
        if item["key"] == "limitation_attested"
    )
    assert limitation["status"] == "resolved_false"
    assert sum(event["kind"] == "clinician_declined" for event in persisted["events"]) == 1
    assert not any(
        fact["source"] == "clinician_attestation"
        for fact in persisted["facts"]
    )


def test_inbound_reply_honors_asked_clinician_item_in_solver_order(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))

    api_requests(("POST", "/api/demo/reset"))
    case = store.get_case("g-rosa")
    clinician_missing = next(
        item for item in case.missing if item.holder.value == "clinician"
    )
    clinician_missing.status = "asked"
    store.save_case(case)
    reply, persisted = api_requests(
        ("POST", "/api/cases/g-rosa/reply", {"json": {"text": ROSA_REPLY}}),
        ("GET", "/api/cases/g-rosa"),
    )

    assert reply.status_code == 200
    assert reply.json()["status"] == "waiting_clinician"
    assert persisted.json()["status"] == "waiting_clinician"


def test_queue_omits_resolved_actions_and_summary_counts_recovered_members(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))
    token = clinician_token("g-rosa")

    (
        _,
        initial_summary,
        _,
        after_reply_summary,
        after_reply_queue,
        _,
        _,
        final_summary,
        final_queue,
    ) = api_requests(
        ("POST", "/api/demo/reset"),
        ("GET", "/api/summary"),
        ("POST", "/api/cases/g-rosa/reply", {"json": {"text": ROSA_REPLY}}),
        ("GET", "/api/summary"),
        ("GET", "/api/queue"),
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "sign"}}),
        ("POST", "/api/cases/g-deshawn/check-database"),
        ("GET", "/api/summary"),
        ("GET", "/api/queue"),
    )

    assert initial_summary.json()["recovered"] == 1
    assert after_reply_summary.json()["recovered"] == 2
    rosa_pending = next(item for item in after_reply_queue.json() if item["id"] == "g-rosa")
    assert rosa_pending["top_missing_fact"]["key"] == "limitation_attested"
    assert final_summary.json()["recovered"] == 3
    completed = {
        item["id"]: item["top_missing_fact"]
        for item in final_queue.json()
        if item["id"] in {"g-rosa", "g-deshawn"}
    }
    assert completed == {"g-rosa": None, "g-deshawn": None}


def test_first_of_two_clinician_signoffs_remains_waiting(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "track-b.sqlite"))
    token = clinician_token("g-rosa")

    api_requests(
        ("POST", "/api/demo/reset"),
        ("POST", "/api/cases/g-rosa/reply", {"json": {"text": ROSA_REPLY}}),
    )
    case = store.get_case("g-rosa")
    rule_pack_version = case.determination_final.rule_pack_version
    case.facts.append(Fact(
        id="fact-g-rosa-in-sud-treatment",
        patient_id=case.patient_id,
        key="in_sud_treatment",
        value=True,
        source=Source.note_span,
        source_ref={"note_id": "n-rosa-sud", "start": 0, "end": 20},
        quote="currently in treatment",
        recorded_at=datetime.now(timezone.utc),
        rule_pack_version=rule_pack_version,
    ))
    case = solver.reevaluate(case)
    store.save_case(case)
    assert [
        item.key for item in case.missing
        if item.holder.value == "clinician" and item.status in ("open", "asked")
    ] == ["limitation_attested", "in_sud_treatment"]

    first, persisted = api_requests(
        ("POST", f"/api/clinician/{token}", {"json": {"decision": "sign"}}),
        ("GET", "/api/cases/g-rosa"),
    )

    assert first.status_code == 200
    assert first.json()["status"] == "waiting_clinician"
    case = persisted.json()
    assert case["status"] == "waiting_clinician"
    assert [
        item["key"] for item in case["missing"]
        if item["holder"] == "clinician" and item["status"] in ("open", "asked")
    ] == ["in_sud_treatment"]
