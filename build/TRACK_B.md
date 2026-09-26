# TRACK B - Product & loop (Person B)

Read PROJECT.md first. Build against fixtures/ until Track A's pipeline lands at 13:30. B2, B3, B4 can run as parallel agents once B1 is up. Owns: api/, web/, engine/loop/, demo/.

## T0b - Repo scaffold (Person B's agent, first 20 min, parallel with T0a)

### Do
1. `api/main.py`: FastAPI app, CORS for localhost:3000, `/api/health`. Stub every route in the golden fixtures table in PROJECT.md returning 501, so the shape is visible.
2. `web/`: Next.js App Router + TypeScript. Routes (empty pages for now): `/` (queue), `/cases/[id]`, `/clinician/[token]`, `/plan` (buyer view), `/eval`. A `lib/api.ts` fetch wrapper pointed at `NEXT_PUBLIC_API_URL`.
3. `web/lib/types.ts`: TypeScript mirror of every model in the Contracts section of `PROJECT.md`. Keep names identical.
4. `Makefile` or `justfile`: `make api`, `make web`, `make pipeline`, `make reset`.
5. `.env.example`: OPENAI_API_KEY, RESEND_API_KEY, DEMO_INBOX (the teammate inbox that stands in for Rosa), NEXT_PUBLIC_API_URL.
6. `.gitignore`: `data/synthea/`, `.cache/`, `.env`, `*.sqlite`.

### Done when
`make api` and `make web` both boot; `/` renders "Queue" and calls `/api/health` successfully.

---

## B1 - API over the store

Owner: Person B. Depends on: T0a, T0b. Blocks: B2, B3, B4.

### Goal
Every endpoint in the Contracts section of `PROJECT.md` returns real shapes, backed by fixtures now and by A's pipeline output after 13:30, with no API code change at the swap.

### Do
1. Implement all non-stretch routes against `engine/store.py`. Start by loading `fixtures/golden_cases.json`.
2. `/summary`, `/run/state`, `/run/evidence`: read counts from the store. These replay a precomputed run - the demo "Run" buttons are a reveal, not a live 1,000-patient LLM job.
3. `/queue`: filter by bucket; `window_days` = renewal_date within N days of AS_OF_DATE; sort by renewal_date asc; include `days_to_renewal`.
4. `/cases/{id}` returns full Case + notes.
5. Clinician tokens: `token = hmac(case_id)` - short, unguessable enough for a demo, no auth.
6. `/demo/reset`: restore golden cases (status, facts, missing, events) to their pre-demo state. Must be instant. We'll hit it before every rehearsal.
7. Every state-changing endpoint appends to `Case.events` with timestamp + kind, so the audit log is real.

### Done when
`curl localhost:8000/api/queue` returns the 7 golden cases sorted by renewal date; `/demo/reset` works; Person A's pipeline output loads without API changes at integration.

---

## B2 - Queue screen + case detail (the operator's screen)

Owner: Person B (agent). Depends on: B1. **P0 - due 13:30.** Functional first; DESIGN.md does the visual pass, so keep styling minimal and tokenized (CSS variables) so Z can restyle fast.

### The user
Clinic enrollment worker. Opens this every morning. Question they're answering: "Who do I need to act on today, and what exactly do I do?"

### Queue (`/`)
- Top: the reveal. Two buttons, "Run the state's check" then "Read the notes". Counters animate from 0 to the value (the ONE orchestrated motion moment in the app). After both: cohort, state exempt, state can't determine, exempt once notes are read, one fact away. Plus verifier-dropped badge (P2).
- Filter tabs by bucket with counts. Toggle "Renewing in 30 days".
- Rows: name, age, language, renewal date + days left, bucket, fragile flag, status, and the top missing fact in plain words with its holder ("Ask Rosa: how long can she stand?" / "Check student enrollment" / "Ask Dr. Patel to sign").
- SAFE rows are visibly quiet. The design point: doing nothing for them is correct.

### Case detail (`/cases/[id]`)
Three columns on a projector-width screen (1440+):
1. **What the state sees**: billed dx list for 12 months, primary codes emphasized, secondary greyed with "not read by the state". Channel A result.
2. **What the chart says**: notes with verified claim spans highlighted. Clicking a claim in the claims list scrolls to and flashes the exact span. Dropped claims shown struck through with the verifier's reason, collapsed by default.
3. **What's missing and who knows it**: MissingFact card: the fact, why it matters, the holder (patient / clinician / database) and one action button, labeled for what it does: "Email Rosa the question" / "Check student enrollment" / "Send to Dr. Patel". Below: the evidence log (every Fact with its source icon + timestamp) and the event timeline.
- Status header updates live after actions (poll 1s or refetch after POST).

### Buyer view (`/plan`)
At-risk members, recovered, fragile count, database-resolved-without-contact count. Fragile list table (P2). Simple.

### Done when
Click through: queue -> Rosa -> spans clickable -> missing-fact card shows patient holder. Works on fixtures and after integration.

---

## B3 - The patient loop: email out, reply in, case flips

Owner: Person B (agent, can run beside B2). Depends on: B1. **P1 - due 15:00. This is the part that wins.**

### Goal
Rosa gets one question in Spanish. She replies in her own words. Her sentence becomes the evidence, logged as hers, and the case flips on screen.

### Outbound (`engine/loop/outbound.py`, `POST /cases/{id}/ask`)
- Take the top open MissingFact with holder=patient. Render the email in `case.language`:
  - Subject: "Your Medi-Cal review on {date}: one question" (localized).
  - Body, max ~70 words: coverage is up for review on {renewal_date}; one question; reply to this email in your own words, any language; no need to log in or fill anything out; clinic name + phone.
  - Never: eligibility statements, case numbers, legal citations, medical terms beyond the patient's own condition name.
- Hand-write the Spanish template for Rosa's question (guaranteed quality). Other languages: LLM translation via `engine/llm.py`, cached.
- Send with Resend. In Resend test mode you can only send to your own verified address, so send to `DEMO_INBOX` and show that inbox on screen as Rosa's. Store `message_id`. Status -> waiting_patient.
- If Resend fails, still record the message and render a preview in the UI. The demo never depends on the network.

### Inbound (`engine/loop/inbound.py`, `POST /cases/{id}/reply` and `/inbound/email`)
- Both routes call one handler. Real inbound webhook is optional; the simulated `/reply` is the demo path (a "Rosa replied" control in the UI that posts her pre-written reply: "Dejé de trabajar en marzo, la espalda no me aguanta más de diez minutos de pie." with an English gloss shown in the UI).
- Parse with an LLM call (cached) into the typed fact the MissingFact asked for: `{key, value, quote, confidence}`. The quote must be a verbatim substring of the reply (check in code, same as A3).
- Store a `Fact(source=patient_reply, quote=..., source_ref={message_id})`. Keep the raw reply on the event log.
- If the reply mentions another registry fact (e.g., "I stopped working in March" -> hours_per_month ~ 0), store it too, same rules.
- Re-run the final determination + bucket + solver for this one case (import from A4; fixture fallback: a small local re-evaluation). Rosa: impairs becomes true via patient self-report -> next missing = limitation_attested / clinician -> status waiting_clinician.
- Low confidence or unparseable -> status stays waiting_patient and the operator sees "Reply needs a human read" with the text. Never guess.

### Database path (`POST /cases/{id}/check-database`)
Deshawn: reads `data/external/school.json`, writes Fact(source=external_db), re-runs, flips to SAFE with event "Resolved from student enrollment. Nobody contacted."

### Done when
Rosa: ask -> email visible -> reply -> case moves to waiting_clinician in under 3 seconds with her sentence in the evidence log tagged "from Rosa". Deshawn flips with one click.

---

## B4 - Clinician card + attestation PDF + audit trail

Owner: Person B (agent). Depends on: B1 (and B3 for Rosa's path). **P1 - due 15:00.**

### Clinician card (`/clinician/[token]`)
The clinician gets 30 seconds. One screen, no navigation, no dashboard:
- One question from the registry: "Does the record support that Rosa's diabetic peripheral neuropathy limits her ability to work?"
- Up to 3 sentences, each with note date, the sentence with the span highlighted, and a link to the full note. For Rosa, include her own reply, clearly labeled "Patient's own words, received {date}".
- Two buttons: "Sign attestation" / "Decline". Nothing to type.
- Copy line under the buttons: "You're attesting to what the record shows. The state makes the eligibility decision."
- Sign -> `Fact(source=clinician_attestation, key=limitation_attested, value=true)`, re-run, status -> attestation_ready. Decline -> status needs_action with event.
- Operator side: "Send to Dr. X" on the case page opens this card (for the demo, in a new tab / side panel).

### Attestation PDF (`engine/loop/pdf.py`, reportlab, `GET /cases/{id}/attestation.pdf`)
Title: **Medical Exemption Attestation** (never "renewal application"). Watermark: "SYNTHETIC DATA - DEMO". Sections:
1. Member: name, synthetic member id, renewal date, rule pack + version.
2. Exemption basis: rule id in plain words + citation from the pack.
3. Evidence table - one row per Fact used: fact, value, source (billing code / clinical note / patient's own words / clinician attestation / external database), exact quote or record reference, date recorded.
4. Clinician attestation: name, statement, signed timestamp.
5. Footer: "Assembled evidence only. Eligibility is determined by the state."
One page. Must render in under a second.

### Audit
Every Fact already carries source, ref, timestamp, rule_pack_version. Make sure every write path in B3/B4 sets all four. The PDF is just a render of the facts.

### Done when
Rosa: sign -> PDF opens with 4 evidence rows from 4 different source types (billing code, note span, patient reply, clinician). Marcus: sign -> PDF with note spans.

---

## B5 - Stretch: voice call + appeal packet (P3, only if ahead)

### Voice (non-responders)
Not a second product: same case object, same MissingFact, same inbound handler.
- Demo version: generate a ~20-second call clip with OpenAI TTS from a script (agent voice asks the same one question in Spanish; patient answer voiced separately), save to `demo/voice_rosa.mp3`, plus a transcript. The UI plays it from the case page under "For people who don't reply to email".
- The transcript goes through the same inbound parser, proving the path is shared.
- Say on stage it's a recorded example of the flow. Don't imply a live phone system.

### Appeal packet
For a case with status "terminated" (add one golden variant or flag Marcus in a copy): same PDF builder, titled "Evidence for appeal", with the 30-day deadline computed from termination date shown at the top. Reuse `pdf.py`; do not fork it.

---

