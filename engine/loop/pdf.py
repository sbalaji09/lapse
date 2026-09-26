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

FACT_LABELS = {
    "qualifying_condition": "Qualifying condition",
    "significantly_impairs": "Significantly limits daily activity",
    "standing_tolerance_minutes": "Can stand for (minutes)",
    "limitation_attested": "Clinician attests the limitation",
    "hours_per_month": "Hours of work or activity per month",
    "monthly_income": "Monthly income",
    "enrolled_half_time_school": "Enrolled at least half time",
    "in_sud_treatment": "In substance use treatment",
    "snap_tanf_work_compliant": "Meets SNAP/TANF work rules",
    "pregnant_or_postpartum": "Pregnant or postpartum",
    "ai_an": "American Indian / Alaska Native",
    "released_incarceration_days": "Days since release",
    "veteran_total_disability": "VA total disability rating",
    "former_foster_youth": "Former foster youth",
    "county_hardship": "Lives in a hardship county",
    "dependent_child_13_or_under": "Cares for a child 13 or under",
    "caregiver_disabled_person": "Cares for a disabled person",
}

_MARGIN = 54
_BOTTOM = 54


def _value(v) -> str:
    return {True: "Yes", False: "No", None: "None on record"}.get(v, str(v)) if isinstance(v, (bool, type(None))) else str(v)


def _record(fact) -> str:
    """The exact words for spans and replies; otherwise a reference someone can look up."""
    if fact.quote:
        return f'"{fact.quote}"'
    ref = fact.source_ref
    if fact.source.value == "billing_code":
        return f"claim {ref.get('claim_id')}, code {ref.get('code')} ({ref.get('display', '')})"
    if fact.source.value == "external_db":
        return f"{ref.get('db')} record {ref.get('record_id') or '(no record)'}"
    if fact.source.value == "clinician_attestation":
        return f"attestation {ref.get('attestation_id', '')}"
    return ", ".join(f"{k}: {v}" for k, v in ref.items())


def evidence_rows(case: Case) -> list[dict]:
    """One row per fact the exemption rests on, plus the clinician's attestation, plus any billed code for the
    condition that the state's primary-diagnosis check never read."""
    from engine import buckets
    from engine.rulepack import load_pack
    from engine.solver import condition_name

    pack = load_pack()
    det = buckets.final_determination(case.patient_id, case.facts, pack)
    used = []
    for rule_id in det.rule_ids:
        for f in buckets.supporting_facts(pack.rule(rule_id), case.facts):
            if f not in used:
                used.append(f)
    used += [f for f in case.facts if f.source.value == "clinician_attestation" and f not in used]
    rows = [{"fact": FACT_LABELS.get(f.key, f.key), "value": _value(f.value),
             "source": SOURCE_LABELS.get(f.source.value, f.source.value), "record": _record(f),
             "date": f.recorded_at.date().isoformat()} for f in used]

    if not any(f.source.value == "billing_code" for f in used):
        words = {w for w in condition_name(case).lower().replace("-", " ").split() if len(w) >= 5}
        billed = [d for d in case.billed_dx_12mo
                  if d.get("sequence", 1) > 1 and words & set(d.get("display", "").lower().split())]
        if billed:
            d = max(billed, key=lambda d: d["date"])
            rows.insert(0, {"fact": "Condition billed", "value": f"Secondary dx #{d['sequence']}",
                            "source": "Billing code",
                            "record": f"claim {d['claim_id']}, code {d['code']} ({d['display']}); "
                                      "not read by the state's primary-diagnosis check",
                            "date": d["date"]})
    return rows


def _build_evidence_pdf(
    case: Case,
    title: str,
    basis_title: str,
    basis_lines: list[str],
    clinician_name: str,
) -> bytes:
    from reportlab.lib.utils import simpleSplit

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER, pageCompression=0)
    width, height = LETTER
    y = height - _MARGIN

    # Watermark first so everything else draws over it.
    c.saveState()
    c.setFillColorRGB(0.93, 0.9, 0.86)
    c.setFont("Helvetica-Bold", 54)
    c.translate(width / 2, height / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, "SYNTHETIC DATA - DEMO")
    c.restoreState()

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
        y -= 8
        line(title, font="Helvetica-Bold", size=12, gap=16)

    c.setFont("Helvetica-Bold", 18)
    c.drawString(_MARGIN, y, title)
    y -= 18
    c.setFillColorRGB(0.72, 0.26, 0.11)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_MARGIN, y, "SYNTHETIC DATA - DEMO")
    c.setFillColorRGB(0, 0, 0)
    y -= 16

    section("Member")
    line(f"Name: {case.display_name}")
    line(f"Synthetic member id: {case.patient_id}")
    line(f"Renewal date: {case.renewal_date.isoformat()}")
    line(f"Rule pack: {case.determination_final.rule_pack_version}")

    section(basis_title)
    for basis_line in basis_lines:
        line(basis_line)

    section("Evidence")
    headers = ["Fact", "Value", "Source", "Exact words or record", "Recorded"]
    col_x = [(0, 128), (128, 70), (198, 92), (290, 160), (450, 58)]
    c.setFont("Helvetica-Bold", 8)
    for (x, _), h in zip(col_x, headers):
        c.drawString(_MARGIN + x, y, h)
    y -= 4
    c.setLineWidth(0.5)
    c.line(_MARGIN, y, width - _MARGIN, y)
    y -= 11
    for row in evidence_rows(case):
        cells = [row["fact"], row["value"], row["source"], row["record"], row["date"]]
        wrapped = [simpleSplit(str(t), "Helvetica", 8, w - 6) for (_, w), t in zip(col_x, cells)]
        rows_needed = max(len(w) for w in wrapped)
        new_page_if_needed(_BOTTOM + rows_needed * 10)
        c.setFont("Helvetica", 8)
        for (x, _), lines_ in zip(col_x, wrapped):
            for i, t in enumerate(lines_):
                c.drawString(_MARGIN + x, y - i * 10, t)
        y -= rows_needed * 10 + 6

    section("Clinician attestation")
    signed = next((f for f in reversed(case.facts) if f.source.value == "clinician_attestation"), None)
    line(f"Clinician: {clinician_name}")
    if signed:
        line(f"{clinician_name} attests that the record supports the evidence above.")
        line(f"Signed: {signed.recorded_at.isoformat(timespec='minutes')}")
    else:
        line("Not yet signed.")

    new_page_if_needed(_BOTTOM + 20)
    y -= 10
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(_MARGIN, y, "Assembled evidence only. Eligibility is determined by the state.")

    c.save()
    return buf.getvalue()


def _basis(rule_id: str) -> list[str]:
    from engine.rulepack import load_pack
    from engine.solver import RULE_NAMES

    pack = load_pack()
    return [f"Exempt through {RULE_NAMES.get(rule_id, rule_id)} (rule: {rule_id})", f"Citation: {pack.citation}"]


def build_attestation_pdf(case: Case, rule_id: str, clinician_name: str) -> bytes:
    return _build_evidence_pdf(
        case,
        "Medical Exemption Attestation",
        "Exemption basis",
        _basis(rule_id),
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
