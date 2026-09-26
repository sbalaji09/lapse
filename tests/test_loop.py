"""The demo flows end to end through the HTTP API, against a throwaway database loaded with the golden cases:
Rosa is asked, replies in Spanish, flips to the clinician, is signed, and gets a PDF; Deshawn is cleared by one
database click; a vague reply is left for a human."""
import pytest
from fastapi.testclient import TestClient

from engine import store


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "loop.sqlite"))
    store.load_fixtures()
    from api.main import app

    return TestClient(app)


ROSA_REPLY = "Dejé de trabajar en marzo, la espalda no me aguanta más de diez minutos de pie."


def test_rosa_full_loop(api):
    assert api.get("/api/cases/g-rosa").json()["bucket"] == "ONE_AWAY"

    ask = api.post("/api/cases/g-rosa/ask").json()
    assert ask["channel"] == "email" and "¿Cuánto tiempo" in ask["preview"]["body"]
    assert "eligib" not in ask["preview"]["body"].lower() and "elegib" not in ask["preview"]["body"].lower()

    reply = api.post("/api/cases/g-rosa/reply", json={"text": ROSA_REPLY}).json()
    assert reply == {"status": "waiting_clinician", "bucket": "PROVABLE", "parsed": True,
                     "fact_key": "standing_tolerance_minutes"}

    rosa = api.get("/api/cases/g-rosa").json()
    assert rosa["determination_final"]["status"] == "exempt"
    words = [f for f in rosa["facts"] if f["source"] == "patient_reply"]
    assert {f["key"] for f in words} == {"standing_tolerance_minutes", "hours_per_month"}
    assert all(f["quote"] in ROSA_REPLY for f in words)                 # her sentence is the evidence
    assert any(e["kind"] == "case_flipped" and e["detail"] == {"from": "ONE_AWAY", "to": "PROVABLE"}
               for e in rosa["events"])
    assert [m["key"] for m in rosa["missing"] if m["status"] == "open"] == ["limitation_attested"]

    from engine.loop.clinician import clinician_token
    token = clinician_token("g-rosa")
    card = api.get(f"/api/clinician/{token}").json()
    assert "neuropathy" in card["question"] and len(card["spans"]) == 3
    assert card["spans"][0]["source"] == "patient_reply" and card["spans"][0]["label"].startswith("Patient's own words")
    assert all(s["quote"] for s in card["spans"])
    signed = api.post(f"/api/clinician/{token}", json={"decision": "sign"}).json()
    assert signed["status"] == "attestation_ready"

    rosa = api.get("/api/cases/g-rosa").json()
    assert {f["source"] for f in rosa["facts"]} >= {"note_span", "patient_reply", "clinician_attestation"}
    # The billing evidence is the neuropathy code the state never read: billed, but only as secondary.
    assert any(d["sequence"] > 1 and "neuropathy" in d["display"].lower() for d in rosa["billed_dx_12mo"])
    assert not [m for m in rosa["missing"] if m["status"] == "open"]
    pdf = api.get("/api/cases/g-rosa/attestation.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    from engine.loop.pdf import evidence_rows
    rows = evidence_rows(store.get_case("g-rosa"))
    assert [r["source"] for r in rows] == ["Billing code", "Clinical note", "Patient's own words", "Clinician attestation"]
    assert "not read by the state" in rows[0]["record"] and b"Medical Exemption Attestation" in pdf.content


def test_deshawn_cleared_by_one_database_click(api):
    before = api.get("/api/cases/g-deshawn").json()
    assert before["bucket"] == "ONE_AWAY" and before["missing"][0]["holder"] == "database"
    out = api.post("/api/cases/g-deshawn/check-database").json()
    assert out == {"status": "no_action", "bucket": "SAFE", "fact_key": "enrolled_half_time_school", "value": True}
    after = api.get("/api/cases/g-deshawn").json()
    assert after["determination_final"]["rule_ids"] == ["school"]
    event = after["events"][-1]
    assert event["kind"] == "database_checked" and "without contacting anyone" in event["detail"]["text"]
    assert not any(e["kind"] == "patient_asked" for e in after["events"])


def test_database_click_on_a_case_without_one_is_a_clear_error(api):
    r = api.post("/api/cases/g-rosa/check-database")
    assert r.status_code == 400 and "database" in r.json()["detail"]


def test_vague_reply_is_left_for_a_human(api):
    api.post("/api/cases/g-rosa/ask")
    r = api.post("/api/cases/g-rosa/reply", json={"text": "No sé, depende del día."}).json()
    assert r["parsed"] is False
    rosa = api.get("/api/cases/g-rosa").json()
    assert rosa["bucket"] == "ONE_AWAY" and rosa["events"][-1]["kind"] == "reply_needs_human_read"


def test_reset_restores_the_demo(api):
    api.post("/api/cases/g-deshawn/check-database")
    assert api.post("/api/demo/reset").json() == {"reloaded": 7}
    assert api.get("/api/cases/g-deshawn").json()["bucket"] == "ONE_AWAY"
