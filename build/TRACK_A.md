# TRACK A - Engines & data (Person A)

Read PROJECT.md first. Do the sections in order; A2 and A3 can run as parallel agents. Owns: engine/ (except engine/loop/), data/, rules/.

## T0a - Shared models + golden fixtures (Person A's agent, first 20 min)

### Goal
Unblock Track B immediately: real pydantic models and 7 fully populated golden cases they can build UI against.

### Do
1. Create `engine/` package, `engine/config.py` (AS_OF_DATE=2027-02-15, MODEL_FAST, MODEL_VERIFY, paths, ACTIVE_RULE_PACK="ca"), and `engine/models.py` exactly as in the Contracts section of `PROJECT.md`.
2. Create `rules/fact_registry.yaml` and `rules/ca.yaml` exactly as in the contracts (A2 fills in details later).
3. Write `fixtures/golden_cases.json`: the 7 golden patients from the golden fixtures table in PROJECT.md as full `Case` objects plus a `notes` array per case.
   - Write each note by hand, 120-250 words, realistic progress-note style (subjective / exam / assessment / plan).
   - Every `Claim.quote` must equal `note.text[start:end]`. Compute offsets with `text.find(quote)`; never type them.
   - Rosa: 3 notes. One mentions neuropathy with numbness in feet, none mention standing or work. Her `missing[0]` is standing_tolerance_minutes with an English and Spanish question.
   - Marcus: 3 verified claims (COPD, exertional dyspnea, half-block limit) so the clinician card has 3 sentences.
   - Bea: include the bait sentence and put the resulting claim in `dropped_claims` with a verifier_reason.
   - `billed_dx_12mo`: realistic, with `sequence` so the UI can show primary vs secondary. Rosa's primary codes are all acute bronchitis/cough; neuropathy appears only as a secondary code or not at all.
   - Renewal dates spread over the next 60 days from AS_OF_DATE; Rosa = 2027-03-04.
4. `engine/store.py`: SQLite with one table per model (JSON column is fine), `load_fixtures()`, `get_case(id)`, `list_cases(filters)`, `save_case(case)`.
5. Test: `python -m engine.store --load-fixtures` loads 7 cases, every quote/offset check passes.

### Done when
`from engine.store import get_case; get_case("g-rosa")` returns a valid `Case`, and the offset assertion runs over all claims with zero failures. Tell Person B it's merged.

---

## A1 - Synthetic cohort, notes and ground truth

Owner: Person A. Depends on: T0a. Blocks: A2, A3 (they can start on fixtures).

### Goal
~1,000 Medicaid expansion adults with (a) FHIR billing data the state sees, (b) generated clinical notes with KNOWN labels, (c) a ground-truth file saying who is truly exempt and why, and (d) mock external databases. Owning the truth is what makes the accuracy chart possible.

### Watch out
- Synthea's `-p N` counts all ages. We need 19-64. Generate ~1,800 and keep the first 993 adults aged 19-64 at AS_OF_DATE, then add the 7 golden patients = 1,000.
- Synthea codes conditions in **SNOMED CT**, not ICD-10. Build the state code list (`rules/codes/ca_frailty.txt`) from SNOMED codes that actually appear in the cohort.
- Synthea claims: primary diagnosis = `Claim.diagnosis` entry with `sequence == 1`. Real billing would add chronic conditions as secondary codes; Synthea often doesn't. That's fine - it strengthens the primary-only flaw, but don't claim it's realistic billing on stage.
- Synthea has no free-text note export (verified 2026-09-24). We generate notes.

### Steps
1. Synthea: `java -jar synthea-with-dependencies.jar -s 42 -p 1800 California` (FHIR R4 on by default). Pin the seed. Output to `data/synthea/`.
2. `engine/cohort.py`: parse bundles -> per patient: demographics (age, sex, race/ethnicity incl. AI/AN via US Core extension, language), conditions with onset/abatement, encounters, claims with sequenced diagnoses in the 12-month lookback before AS_OF_DATE, pregnancies.
3. Social/truth generator (`data/truth/{patient_id}.json`), seeded, with these target rates (tune so buckets land roughly where the pitch expects, then report the real numbers):
   - income >= $580 (in state wage records): ~15%
   - hours >= 80 known only to the patient: ~10%
   - dependent child <=13: ~12%; caregiver of disabled person: ~3%
   - SNAP work-compliant (state-visible): ~5%; half-time student (NOT state-visible): ~4%
   - SUD treatment: ~3%; recent release: ~1%; foster youth: ~1% of under-26; veteran total disability: ~1%; county hardship: set 1 county
   - Medically frail truth: derived from conditions + injected impairment level (below)
   Each truth fact records `holders` (who knows it) and `state_visible: bool`.
4. `engine/notes.py`: for each patient generate 2-4 progress notes over the lookback window from templates keyed on their actual conditions and encounters (fast, deterministic, no LLM needed; LLM rewrite optional for golden patients only).
   Inject sentences from a hand-authored **impairment library** (`data/impairment_library.yaml`, ~60 sentences) verbatim, recording offsets and labels:
   - positive, qualifying + impairs ("Unable to stand longer than 10 minutes due to neuropathic pain.")
   - qualifying, impairment unstated ("Diabetic peripheral neuropathy, stable on gabapentin.") -> the ONE_AWAY population
   - negatives/distractors: negation ("Denies difficulty walking."), resolved ("Back pain resolved."), family history ("Mother has severe arthritis."), past ("Was on crutches in 2019.")
   Save labels to `data/truth/labels.jsonl`: `{note_id, start, end, category, qualifying, impairs, polarity}`.
5. Mock external DBs in `data/external/`: `snap.json`, `school.json`, `corrections.json`, `va.json`, `county.json`, `state_wage_records.json` - keyed by patient_id, generated from truth.
6. Force-insert the 7 golden patients from `fixtures/golden_cases.json` with the same ids and notes.
7. Assign `renewal_date` uniformly over AS_OF_DATE .. +6 months; `clinic_id` among 3 clinics; `language` from Synthea (keep ~25% es).
8. `python -m engine.pipeline --stage cohort` writes everything to the store.

### Done when
Cohort = 1,000 adults 19-64, every label offset matches its note text, golden ids present, and a printout of truth rates.

---

## A2 - Channel A: the State Simulator

Owner: Person A. Depends on: A1 format (start on fixtures). Blocks: A4.

### Goal
A faithful, deterministic, zero-LLM implementation of the state's ex parte check. This is the baseline we beat and the honest basis for "the state would miss this person". If it isn't faithful, the whole pitch falls over.

### Rules
- No LLM. No notes. Read only sources listed in the rule pack's `state_visible_sources`.
- Medical frailty via `primary_dx_only`: in the lookback window, any claim with `sequence == 1` whose code is on the code list -> `qualifying_condition = true` AND `significantly_impairs = true` (the state treats the code as sufficient). Nothing else counts.
- Compliance and other exemptions: evaluate from state-visible facts only (wage records -> income, SNAP, corrections, demographics, pregnancy claims).
- Output: `Determination(channel="A")` with status `compliant | exempt | not_determined`, `rule_ids`, and a `Fact` for every piece of data used (source=billing_code / structured_record / external_db with `source_ref`).

### Steps
1. `engine/rulepack.py`: load YAML, parse `requires` expressions (`a`, `a | b`, `x >= 80`, `x <= 90`) into a tiny evaluator over `dict[key, Tri|value]`. Three-valued: unknown stays unknown. Share this evaluator with A4.
2. Complete `rules/ca.yaml` and `rules/codes/ca_frailty.txt` (SNOMED codes for: CKD 4-5, heart failure, COPD severe, blindness, paralysis, schizophrenia, bipolar, major depressive disorder, opioid/alcohol use disorder, intellectual disability, cancer on active treatment, etc. - whatever appears in the cohort). Comment each with the plain name.
3. `engine/channel_a.py`: `run_channel_a(patient, pack) -> Determination`.
4. Unit tests on golden patients: Linh exempt (CKD4 primary), Karen exempt (MDD primary), Omar compliant (income), Rosa not_determined, Marcus not_determined, Deshawn not_determined.
5. Log counts: `a_exempt`, `a_compliant`, `a_not_determined`.

### Done when
Runs over 1,000 in seconds, golden tests pass, every exempt determination has a fact with a billing_code source_ref pointing to a real claim.

---

## A3 - Channel B: Evidence Finder + Verifier

Owner: Person A (can be a second agent running beside A2). Depends on: A1 format. Blocks: A4.

### Goal
Read notes, extract impairment claims that point at exact text, then have an independent verifier throw out anything the quoted text doesn't support. "We would rather miss someone than invent proof."

### `engine/llm.py` (write first; Track B reuses it)
- `call_json(model, system, user, schema) -> dict` using OpenAI structured outputs / JSON schema.
- Disk cache in `.cache/llm/{sha256(model+system+user)}.json`. Cache hit = no network. Demo runs must work offline after one warm run.
- Async batch helper with concurrency 16 and retry on 429.

### Extraction (`engine/channel_b.py`)
- One call per note. Input: note text only. Output list of `{category, condition, qualifying_category, significantly_impairs: "true"|"false"|"unknown", quote}`.
- Prompt rules: quote must be copied verbatim from the note, one sentence or clause; ignore negations, resolved issues, family history, past history; `significantly_impairs` is "true" only if the text states a functional limitation (standing, walking, lifting, concentrating, working, ADLs); if the condition is present but function isn't described, "unknown".
- **Do offsets in code, never ask the model for them**: `start = note.text.find(quote)`; if -1, try whitespace-normalized match; if still not found, drop the claim and count it as `unlocatable`.

### Verifier (`engine/verifier.py`)
- Separate call, different system prompt, ideally the stronger model. Input: ONLY `{claim: "<condition>, qualifying=<>, impairs=<>", span: "<quote>"}`. No note, no patient.
- Output `{supported: bool, reason: str}`. Also check: is the subject the patient (not a relative)? is it current?
- `verified=False` claims move to `Case.dropped_claims`. Count them for the badge.

### Aggregate to a determination
Per patient, combine Channel A facts + verified claims into facts `qualifying_condition` and `significantly_impairs` (source=note_span). Produce `Determination(channel="B")`.

### Steps
1. Build and run on the 7 golden patients first. Bea's bait must be dropped by the verifier. Rosa must come out qualifying=true, impairs=unknown.
2. Run over the full cohort (~3,000 notes). Print cost/time estimate before the full run.
3. Log: claims extracted, verified, dropped, unlocatable.

### Done when
Full cohort processed and cached; every stored claim satisfies `note.text[start:end] == quote`; golden expectations hold.

---

## A4 - Buckets + Counterfactual Solver

Owner: Person A. Depends on: A2, A3. Blocks: B2/B3 real data (they have fixtures until then). **P0 - due 13:30.**

### Goal
Sort everyone into SAFE / PROVABLE / ONE_AWAY / NO_PATH (+ fragile flag), and for everyone not safe, find the single missing fact that would flip them and who holds it. This is a search over rules, not a prompt. Zero LLM.

### Final determination
Merge facts from A (state-visible) and B (verified notes). Evaluate the rule pack with the shared three-valued evaluator from A2 -> `Determination(channel="final")`.

### Buckets (`engine/buckets.py`)
- SAFE: A is compliant or exempt.
- fragile flag: SAFE via `medically_frail` in A, and Channel B has zero verified claims supporting qualifying AND impairs for that condition.
- PROVABLE: not SAFE, and final is exempt on evidence already in the chart (needs clinician signature to become an attestation).
- ONE_AWAY: not SAFE/PROVABLE, and solver finds a missing set of size 1.
- NO_PATH: everything else.

### Solver (`engine/solver.py`)
For each rule (compliance and exemptions) in the pack:
1. Evaluate each predicate against known facts: true / false / unknown.
2. If any predicate is known false -> rule is dead.
3. Otherwise the unknown predicates are that rule's missing set.
Pick the smallest missing set across live rules. Tie-break by holder cost: database (0) < clinician (1) < patient (2), then by truth-independent prior (don't peek at truth!). Size 1 -> ONE_AWAY.

Emit `MissingFact` with: key, holder (first holder in registry that applies), database name if any, `unlocks_rule`, a one-sentence `why` for the operator ("Neuropathy is in her notes, but nothing says how it limits her. If she can't stand for long, she qualifies as medically frail."), and `question` per language from the registry with `{name}`/`{condition}` filled.

Special case, medically frail: if `qualifying_condition = true` and `significantly_impairs = unknown`, the missing fact is a functional one (standing_tolerance_minutes etc.) held by the patient, and after the patient answers, `limitation_attested` held by the clinician. Represent the second as the next item in `missing`.

### Database-held facts
`resolve_database(case, missing_fact)` reads `data/external/*.json` and writes a Fact with source=external_db. Run automatically in the pipeline for every database-held missing fact EXCEPT the golden Deshawn (leave his for the live demo click). Log how many people were resolved without contacting anyone - that's a pitch line.

### Pipeline
`python -m engine.pipeline` = cohort -> A -> B -> verify -> final -> buckets -> solver -> database auto-resolve -> store. Idempotent, uses caches. Prints the summary JSON from the `/summary` contract.

### Done when
Summary prints; golden cases land in their contracted buckets with the contracted top missing fact; the store is populated so B's API can drop fixtures. **Ping Person B for integration.**

---

## A5 - Accuracy eval + fragile list

Owner: Person A. Depends on: A4. **P2 - due 16:00.**

### Goal
Show a real accuracy number against labels we wrote, next to the state's method. And the list of people the state exempted with nothing behind it.

### `engine/eval.py`
Patient level (label = truly exempt-or-compliant per truth file, using ALL truth facts):
- State method (Channel A): precision, recall, F1, confusion matrix.
- Ours (final, before any outreach): same.
- Ours after outreach, simulated: resolve every ONE_AWAY by answering from truth -> recall. Label this clearly as simulated.
Claim level (against `labels.jsonl`, match = same note and span overlap >= 50%): precision, recall, F1 for impairment detection; verifier kept vs dropped; how many dropped claims were true negatives in the labels (verifier value).

Write `data/eval.json` in the `/eval` contract shape. Print a short table.

### Fragile
`fragile` cases list with the claim code the state used and "no supporting sentence found in N notes". Karen must appear.

### Numbers for the pitch
Print, in one block, the five demo numbers: cohort, A exempt, A not determinable, final exempt (delta = "already qualify but get dropped"), ONE_AWAY count, fragile count, database-resolved-with-no-contact count. DESIGN.md copies these into pitch.md.

### Done when
`data/eval.json` exists and `/api/eval` serves it.

---

## A6 - Stretch: Nebraska rule pack (P3, only if ahead)

### Goal
Show that states are config, not code: switch California -> Nebraska mid-demo and the numbers change.

### Do
1. `rules/ne.yaml`: same structure; self-declaration of medical frailty and SUD treatment allowed; primary-dx-only ex parte; its own code list file. Put `(verify)` next to any rule detail you aren't sure of.
2. `POST /api/rulepack/{state}` (coordinate with Person B): reload pack, re-run A + final + buckets + solver from cache (no new LLM calls), return summary.
3. Test the swap takes < 3 seconds.

---

