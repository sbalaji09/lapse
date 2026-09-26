# Voice Agent Feature Specification

**Status:** Proposed  
**Date:** 2026-09-26  
**Owner:** Track B / patient loop  
**Primary service:** Amazon Bedrock, Amazon Nova 2 Sonic  
**Related seams:** `engine/loop/voice.py`, `engine/loop/inbound.py`, `api/main.py`, `web/app/cases/[id]/page.tsx`

## 1. Summary

The Lapse voice agent contacts a patient when the counterfactual solver has identified one patient-held fact that could resolve the case and the patient has not answered by email.

The agent conducts a short conversation in the patient's language:

1. Explain that the clinic has one question about the patient's coverage review.
2. Ask the exact question selected from the case's open `MissingFact`.
3. Allow the patient to answer naturally and interrupt the agent.
4. Ask at most one neutral clarification if the answer is incomplete.
5. Thank the patient and end the conversation.
6. Send the exact final patient transcript through the existing `handle_voice_transcript` path.
7. Let the deterministic rules, bucket logic, and solver decide what happens next.

For Rosa, the agent asks in Spanish how long she can stand before sitting. Her answer, such as “solo puedo estar de pie diez minutos,” becomes a `patient_reply` fact. The solver then moves the case from `ONE_AWAY` to `PROVABLE` and routes it to the clinician for signature.

The voice model is a conversation transport and transcription layer. It never determines eligibility and never writes a determination.

## 2. Why This Feature Exists

The people most likely to lose coverage for procedural reasons are also the least likely to complete a portal form. Email alone will not reach everyone.

Voice gives Lapse a second low-friction channel that:

- does not require a login, smartphone app, or form;
- asks only one question instead of conducting a broad intake;
- works in the patient's preferred language;
- preserves the patient's own words as evidence;
- reuses the same rule engine, provenance, clinician handoff, and PDF flow as email;
- gives non-responders another chance without asking for information available from a database.

## 3. Goals

- Complete one short, natural voice interview in English or Spanish.
- Use Nova 2 Sonic for bidirectional audio, turn taking, interruption handling, speech generation, and ASR transcription.
- Ask only the current patient-held `MissingFact`.
- Store exact patient words and voice-session provenance.
- Reuse `handle_voice_transcript`; do not create a second fact-writing path.
- Re-run the existing deterministic solver after the transcript is accepted.
- Surface live state and transcript to the operator.
- Work in a local fallback mode when Bedrock is unavailable.
- Keep AWS credentials and Bedrock traffic off the browser.

## 4. Non-Goals

- The agent does not determine or announce eligibility.
- The agent does not diagnose, interpret symptoms, or state a clinical conclusion.
- The agent does not search policy or answer general benefits questions.
- The agent does not choose which fact to ask for; the solver already chose it.
- The agent does not ask for a fact that can be obtained from an available database.
- The first version does not place a real telephone call through Amazon Connect.
- The first version does not retain raw audio by default.
- The first version does not replace the Channel B evidence finder or verifier.
- The first version does not add an autonomous agent framework, AgentCore, or long-term memory.

## 5. Users and Surfaces

### Patient

Receives a brief voice conversation in their preferred language. No dashboard, login, form, legal language, or eligibility statement is presented.

### Clinic enrollment worker

Starts or previews the voice conversation from the case page, sees its current state, watches the transcript arrive, and sees whether the reply resolved the missing fact or needs human review.

### Clinician

No new workflow. When the voice reply establishes the missing patient fact, the existing clinician card includes the patient's words and allows the clinician to sign or decline.

## 6. Eligibility for Voice Outreach

A case can start a voice session only when all of the following are true:

- the case is `ONE_AWAY`;
- the top unresolved `MissingFact.holder` is `patient`;
- the case has a phone number for a real deployment, or is a synthetic demo case;
- no available database can answer the fact;
- there is no completed voice answer for that missing-fact ID;
- outreach has not been declined;
- the configured call-attempt limit has not been reached.

The API must reject voice sessions for `SAFE`, `PROVABLE`, `NO_PATH`, database-held, or clinician-held steps.

## 7. Conversation Behavior

### Opening

The agent identifies the clinic, asks whether it is speaking with the intended recipient, and states that it has one question about a coverage review. In a real deployment, the agent must disclose transcription or recording according to clinic policy and obtain any required consent before continuing.

The demo uses synthetic data and may use a shortened opening.

### Question

The question is copied exactly from:

```python
case.missing[0].question[case.language]
```

The model must not rewrite the substantive meaning of the question. It may add a short conversational lead-in.

### Clarification

The agent may ask one clarification only when the answer does not contain enough information for the requested fact.

Examples:

- “Was that ten minutes or ten hours?”
- “About how many minutes can you stand?”
- “Could you say that one more time?”

It may not introduce a second registry fact or conduct a general interview.

### Completion

When a usable answer is heard, the agent thanks the patient and ends. The final ASR transcript of the patient's words is submitted to Lapse.

If the patient refuses, withdraws consent, asks to stop, or cannot answer, the agent ends politely. Refusal or silence must never be interpreted as a false fact.

### Questions from the patient

If the patient asks whether they qualify, whether coverage will end, or what a medical answer means, the agent says:

> I can’t make that decision. I’m only collecting your answer for the clinic, and the clinic can follow up with you.

It then repeats the single question once or offers to end the call.

## 8. Conversation Policy

The runtime system prompt must enforce:

- Speak in the configured language unless the patient clearly switches languages.
- Ask exactly the supplied question.
- Keep each response to one or two short sentences.
- Never use “eligible,” “ineligible,” “approved,” “denied,” “exempt,” or equivalent conclusions about the patient.
- Never claim the patient has a diagnosis or functional limitation.
- Never infer, summarize, or alter the patient's answer before storage.
- Ask at most one clarification.
- Stop immediately when asked.
- Do not request SSN, member ID, financial account data, medication details, or unrelated health information.
- Do not reveal notes, diagnoses, rule IDs, bucket names, or internal case state.

The application must also scan assistant text output for prohibited eligibility language. If prohibited output is detected, audio playback stops, the session is closed, and the event is logged for review.

## 9. Architecture

```text
Case detail page
      |
      | Socket.IO: start, PCM audio, stop
      v
Voice relay service (`voice/`, Node.js)
      |
      | InvokeModelWithBidirectionalStream
      v
Amazon Nova 2 Sonic
      |
      | audio output + final USER ASR transcript
      v
Voice relay service
      |
      | POST /api/cases/{id}/voice/reply
      v
FastAPI
      |
      v
engine.loop.voice.handle_voice_transcript
      |
      v
engine.loop.inbound.handle_reply
      |
      v
deterministic reevaluation and solver
```

### Why a server-side relay

- AWS credentials must never be sent to the browser.
- Nova Sonic uses a persistent bidirectional Bedrock stream.
- The relay can validate the case, build the prompt, accumulate final transcript events, enforce time limits, and close abandoned sessions.
- The official AWS Node sample already demonstrates browser audio, Socket.IO, Bedrock streaming, transcription, and interruption handling.

### Audio

- Browser to relay: 16 kHz, 16-bit, mono LPCM frames.
- Nova output: 24 kHz, 16-bit, mono LPCM.
- Audio is streamed in real time.
- Raw audio is not persisted in the MVP.
- Product target: under two minutes per interview.
- Hard session timeout: seven minutes, below Nova's eight-minute connection limit.

## 10. Components

### `voice/` relay service

New isolated Node.js service adapted from the official AWS Nova 2 Sonic WebSocket example.

Responsibilities:

- authenticate to Bedrock using the AWS default credential provider;
- fetch the case from FastAPI using only a case ID supplied by the browser;
- verify that voice outreach is allowed;
- construct the constrained system prompt;
- start Nova speaking with the fixed question;
- forward browser audio to Bedrock;
- forward Nova audio and state updates to the browser;
- collect only `FINAL` `USER` transcription events;
- handle barge-in by clearing queued assistant audio;
- submit the completed transcript to FastAPI;
- close sessions on completion, cancellation, timeout, or disconnect.

The relay has no eligibility or fact-extraction logic.

### FastAPI

Add voice session and transcript endpoints. FastAPI remains the only service allowed to mutate a case.

### Existing engine

`engine/loop/voice.py` remains the entry point for a completed voice transcript. It delegates to `handle_reply`, which records facts and re-runs the solver.

### Web

The case page exposes the feature only for an open patient-held missing fact.

## 11. API Contract

### Start a session

```http
POST /api/cases/{case_id}/voice/session
```

Response:

```json
{
  "session_id": "voice-uuid",
  "case_id": "g-rosa",
  "language": "es",
  "question": "¿Cuánto tiempo puede estar de pie antes de necesitar sentarse?",
  "missing_fact_id": "m-g-rosa-standing_tolerance_minutes",
  "status": "ready",
  "expires_in_seconds": 420
}
```

This endpoint validates eligibility for voice outreach and appends a `voice_session_created` event. It does not expose notes or determinations to the voice service.

### Submit the final transcript

```http
POST /api/cases/{case_id}/voice/reply
Content-Type: application/json
```

Body:

```json
{
  "session_id": "voice-uuid",
  "transcript": "Solo puedo estar de pie diez minutos.",
  "language": "es",
  "model_id": "amazon.nova-2-sonic-v1:0",
  "recording_ref": null
}
```

Behavior:

```python
handle_voice_transcript(
    case_id=case_id,
    transcript=body.transcript,
    recording_ref=body.recording_ref,
    message_id=body.session_id,
)
```

Response uses the existing reply shape:

```json
{
  "status": "waiting_clinician",
  "bucket": "PROVABLE",
  "parsed": true,
  "fact_key": "standing_tolerance_minutes"
}
```

An unparseable answer returns `parsed: false` and creates a `reply_needs_human_read` event.

### Cancel a session

```http
POST /api/cases/{case_id}/voice/session/{session_id}/cancel
```

Cancellation records the reason but creates no fact.

## 12. Socket Events

Browser to relay:

- `initializeConnection`
- `startVoiceSession { caseId, sessionId }`
- `audioInput { pcm }`
- `stopVoiceSession`
- `cancelVoiceSession`

Relay to browser:

- `voiceState { state }`
- `assistantTranscript { text, final }`
- `patientTranscript { text, final }`
- `audioOutput { pcm }`
- `voiceCompleted { result }`
- `voiceError { code, message, retryable }`

The browser may display transcripts, but the relay submits the server-observed final patient transcript. The browser does not decide what text is stored.

## 13. State Model

Voice-session state is ephemeral and does not require a new shared `CaseStatus`.

```text
idle
  -> connecting
  -> greeting
  -> listening
  -> clarifying (optional, once)
  -> submitting
  -> completed

Any active state
  -> cancelled
  -> declined
  -> needs_human
  -> failed
  -> timed_out
```

The case remains `waiting_patient` while the voice session is active. The existing inbound handler changes case state only after a transcript produces a fact.

## 14. Provenance and Audit Events

Every accepted fact remains:

```python
Fact(
    source=Source.patient_reply,
    quote=<exact substring from transcript>,
    source_ref={
        "message_id": <voice session ID>,
        "channel": "voice",
        "recording_ref": <optional retained audio reference>
    }
)
```

No shared model change is required for the MVP.

Recommended events:

- `voice_session_created`
- `voice_session_started`
- `voice_patient_spoke`
- `voice_clarification_asked`
- `voice_session_completed`
- `voice_session_declined`
- `voice_session_cancelled`
- `voice_session_failed`
- `voice_policy_blocked`
- existing `patient_reply_received`
- existing `reply_needs_human_read`
- existing `case_flipped`

Event details may include session ID, missing-fact ID, language, model ID, duration, transcript, completion reason, and error code. AWS credentials and raw audio bytes must never be logged.

## 15. Privacy and Safety

- Synthetic data only during the demo.
- Use only AWS services covered by the applicable BAA before processing real PHI.
- Keep Bedrock and application resources in an approved US region.
- Do not put patient data in model prompts beyond first name, language, clinic name, and the single approved question.
- Do not send clinical notes, claims, determinations, or rule-pack details to the voice model.
- Do not retain audio unless the clinic explicitly enables recording and defines consent, encryption, access, and retention policy.
- Encrypt transport with HTTPS/WSS.
- Restrict the relay's IAM role to the configured Sonic model and required logging.
- Redact transcripts from ordinary application logs; store them only in the case audit record.
- A failed or incomplete conversation never writes a negative fact.

## 16. Failure and Fallback Behavior

| Failure | Behavior |
|---|---|
| Bedrock credentials or model access unavailable | Show “Voice service unavailable”; keep email and manual-reply paths active |
| Browser microphone denied | Offer the transcript simulator or email path |
| Patient cannot hear audio | Allow one reconnect, then fall back to a human callback |
| Network disconnect | Close the Bedrock stream; do not submit a partial transcript automatically |
| Transcript is unclear | One clarification; then `reply_needs_human_read` |
| Patient refuses or asks to stop | End immediately; record refusal without a fact |
| Prohibited assistant output | Stop playback, close session, log `voice_policy_blocked` |
| Session reaches time limit | End politely and route to human review |
| FastAPI submission fails | Retain the final transcript in relay memory briefly and retry idempotently with the same session ID |

The demo must include a local transport that emits the same socket events using a prerecorded or typed transcript. This is visibly labeled as a fallback and must exercise the real FastAPI and solver path.

## 17. Configuration

```dotenv
VOICE_BACKEND=local
VOICE_MODEL_ID=amazon.nova-2-sonic-v1:0
AWS_REGION=us-east-1
VOICE_RELAY_ORIGIN=http://localhost:8081
VOICE_API_ORIGIN=http://localhost:8000
VOICE_SESSION_MAX_SECONDS=420
VOICE_RETAIN_AUDIO=0
```

`VOICE_BACKEND=local` is the default so tests and the recorded demo do not require AWS. `VOICE_BACKEND=aws` enables Nova 2 Sonic.

The model ID and region live only in configuration and must be verified against current Bedrock availability before deployment.

## 18. Operator UI

When voice is available, the patient-held missing-fact card shows:

- “Call Rosa with this question”
- language and exact question preview;
- a note that the call will not discuss eligibility;
- Start, Stop, and Use recorded example controls;
- current state: Connecting, Speaking, Listening, Processing, Complete;
- live assistant and patient transcript;
- completion result:
  - “Answer recorded; ready for clinician”
  - “Reply needs a human read”
  - “Call ended without an answer”

After completion, the page reloads the case so the bucket, missing fact, evidence log, and clinician action update from server state.

## 19. MVP Implementation Cut

The demo-sized vertical slice includes:

1. AWS credential and model-access smoke test.
2. `voice/` relay adapted from the official Node WebSocket sample.
3. English and Spanish constrained prompts.
4. Browser microphone input and audio playback.
5. Final patient transcript accumulation.
6. FastAPI start and reply routes.
7. Reuse of `handle_voice_transcript`.
8. Case-page voice panel.
9. Local recorded/typed fallback using the same transcript endpoint.
10. Tests for Rosa's Spanish path and all rejection conditions.

The MVP may use an in-browser synthetic call instead of a real telephone number. That still demonstrates the Bedrock speech-to-speech path and the Lapse case transition.

## 20. Later Phases

### Real outbound calls

Connect the same session logic to Amazon Connect. Calls occur only during configured local hours, use a neutral voicemail, respect retry limits, and apply clinic consent policy.

### Bedrock reply interpreter

Replace the narrow keyword parser with a structured Bedrock call that proposes multiple registry facts from email or voice transcripts. Code must verify:

- every key exists in the registry;
- every quote is a verbatim transcript substring;
- values match registry types;
- low-confidence facts go to human review;
- the model proposes facts but never saves them directly.

### Non-responder automation

Schedule voice outreach after an unanswered email, with operator-configured timing, call limits, quiet hours, language, and opt-out handling.

### Voice quality and accessibility

- slower speech option;
- replay question;
- keypad fallback;
- interpreter handoff;
- transcript correction by the operator;
- support for additional languages only when both the question and voice quality are reviewed.

### Metrics

- connection success;
- time to first audio;
- time to first transcript;
- interview duration;
- clarification rate;
- parse success rate;
- human-review rate;
- patient refusal rate;
- case-flip rate;
- failures by stage;
- prohibited-output blocks.

## 21. Acceptance Criteria

### Product

- An operator can start a synthetic voice conversation from Rosa's case.
- The agent speaks Spanish and asks the exact registered question.
- The patient can interrupt the agent.
- The agent asks no more than one clarification.
- The agent never makes an eligibility or clinical conclusion.
- Rosa's final words appear in the evidence log as a voice `patient_reply`.
- Rosa moves from `ONE_AWAY` to `PROVABLE` and `waiting_clinician`.
- The clinician card shows Rosa's own words first.
- An unclear answer creates no fact and is routed to human review.
- Email and manual reply flows continue to work unchanged.

### Technical

- Browser code contains no AWS credentials.
- The relay uses `InvokeModelWithBidirectionalStream`.
- Only final `USER` transcript events are submitted.
- Repeated submission of the same session ID is idempotent.
- No partial transcript is saved after a disconnect.
- Raw audio is not persisted by default.
- Local tests require no AWS account.
- The full existing Python suite remains green.
- The web production build remains green.

## 22. Demo Story

1. Open Rosa's case and show that neuropathy appears in the chart, but the record does not say how it affects daily life.
2. Click “Call Rosa with this question.”
3. Nova greets Rosa in Spanish and asks how long she can stand.
4. Answer: “Dejé de trabajar en marzo; solo puedo estar de pie diez minutos.”
5. Show the live transcript.
6. The call ends and the same voice transcript handler used by the recorded fallback stores the answer.
7. The case visibly changes from `ONE_AWAY` to `PROVABLE`.
8. Open the clinician card, where Rosa's own words appear first.
9. State explicitly: the voice model collected her words; deterministic code applied the rule; the clinician still makes the attestation.

## 23. Open Decisions

- Whether the first live implementation should retain audio for the demo or store transcript only. Recommendation: transcript only.
- Whether the relay lives as its own `voice/` service or is later folded into the API deployment. Recommendation: isolated service for the MVP.
- Which identity-confirmation and recording-consent flow clinics require before real patient use.
- Whether a patient can request a human callback from the voice experience.
- The exact retry and quiet-hours policy for future Amazon Connect outreach.
