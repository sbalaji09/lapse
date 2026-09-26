import asyncio
from datetime import date, timedelta

import httpx

from api.main import app
from engine import store
from engine.loop.pdf import build_appeal_pdf, appeal_deadline


def test_appeal_deadline_is_exactly_30_days():
    termination_date = date(2027, 2, 1)

    assert appeal_deadline(termination_date) == termination_date + timedelta(days=30)


def test_appeal_pdf_has_title_and_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "appeal.sqlite"))
    store.load_fixtures()
    case = store.get_case("g-marcus")

    data = build_appeal_pdf(case, date(2027, 2, 1), case.clinician_name)

    assert data.startswith(b"%PDF")
    assert b"Evidence for appeal" in data
    assert b"Termination date: 2027-02-01" in data
    assert b"Response deadline: 2027-03-03" in data


def test_appeal_pdf_returns_404_for_missing_case(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPSE_DB", str(tmp_path / "appeal.sqlite"))

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/api/cases/missing/appeal.pdf",
                params={"termination_date": "2027-02-01"},
            )

    response = asyncio.run(request())

    assert response.status_code == 404
    assert response.json() == {"detail": "case not found"}
