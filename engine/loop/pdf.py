"""Assemble evidence PDFs. Synthetic demo data only."""
import io
from datetime import date, timedelta

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from engine.models import Case

SOURCE_LABELS = {
    "billing_code": "Billing code",
    "structured_record": "Structured record",
    "note_span": "Clinical note",
    "patient_reply": "Patient's own words",
    "clinician_attestation": "Clinician attestation",
    "external_db": "External database",
}

_MARGIN = 72
_BOTTOM = 72


def _build_evidence_pdf(
    case: Case,
    title: str,
    basis_title: str,
    basis_lines: list[str],
    clinician_name: str,
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER, pageCompression=0)
    width, height = LETTER
    y = height - _MARGIN

    def new_page_if_needed(min_y: float = _BOTTOM):
        nonlocal y
        if y < min_y:
            c.showPage()
            y = height - _MARGIN

    def line(text: str, font: str = "Helvetica", size: int = 10, gap: int = 14):
        nonlocal y
        new_page_if_needed()
        c.setFont(font, size)
        c.drawString(_MARGIN, y, text)
        y -= gap

    def section(title: str):
        nonlocal y
        y -= 6
        line(title, font="Helvetica-Bold", size=12, gap=16)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(_MARGIN, y, title)
    y -= 22

    c.setFillColorRGB(0.7, 0, 0)
    c.setFont("Helvetica", 8)
    c.drawString(_MARGIN, y, "SYNTHETIC DATA - DEMO")
    c.setFillColorRGB(0, 0, 0)
    y -= 20

    section("Member")
    line(case.display_name)
    line(f"Synthetic member id: {case.patient_id}")
    line(case.renewal_date.isoformat())
    line(case.determination_final.rule_pack_version)

    section(basis_title)
    for basis_line in basis_lines:
        line(basis_line)

    section("Evidence")
    for fact in case.facts:
        label = SOURCE_LABELS.get(fact.source.value, fact.source.value)
        detail = (fact.quote or str(fact.source_ref))[:70]
        line(f"- {fact.key} = {fact.value}  [{label}]  {detail}")

    section("Clinician attestation")
    line(f"{clinician_name} attests the record supports the above.")

    new_page_if_needed(_BOTTOM + 20)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(_MARGIN, y, "Assembled evidence only. Eligibility is determined by the state.")

    c.save()
    return buf.getvalue()


def build_attestation_pdf(case: Case, rule_id: str, clinician_name: str) -> bytes:
    return _build_evidence_pdf(
        case,
        "Medical Exemption Attestation",
        "Exemption basis",
        [rule_id],
        clinician_name,
    )


def appeal_deadline(termination_date: date) -> date:
    return termination_date + timedelta(days=30)


def build_appeal_pdf(case: Case, termination_date: date, clinician_name: str) -> bytes:
    return _build_evidence_pdf(
        case,
        "Evidence for appeal",
        "Appeal dates",
        [
            f"Termination date: {termination_date.isoformat()}",
            f"Response deadline: {appeal_deadline(termination_date).isoformat()}",
        ],
        clinician_name,
    )
