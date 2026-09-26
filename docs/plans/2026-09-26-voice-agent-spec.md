# Automatic Reminder Call Specification

**Status:** Implemented
**Date:** 2026-09-26
**Phone provider:** Twilio Programmable Voice
**Local fallback:** Built-in call simulator

## Purpose

When a patient does not answer an email, Lapse calls one configured test or
operations phone number and reads a fixed reminder to check the email and
submit the requested information.

This is intentionally a one-way reminder, not a conversational voice agent.
The call does not collect or interpret speech. Only the existing email reply
flow can add patient facts or advance a case.

## Fixed messages

English:

> Hello, this is an automated reminder from your clinic. Please check your
> email and submit the information we requested. If you need help, call your
> clinic. Thank you.

Spanish:

> Hola, este es un recordatorio automático de su clínica. Por favor revise su
> correo electrónico y envíe la información que solicitamos. Si necesita ayuda,
> llame a su clínica. Gracias.

Messages are application constants. They contain no diagnosis, eligibility
result, missing fact, deadline, or other case-specific clinical detail.

## Operating policy

- Initial call: 24 hours after the patient email.
- Retry: 24 hours after an unanswered or failed call.
- Attempt limit: two calls for the current missing fact.
- Calling window: 09:00–18:00 `America/Los_Angeles`.
- Active-call timeout: 10 minutes.
- Any email reply after the ask stops automatic calling.
- A completed reminder stops further calls and leaves the case unchanged.
- A no-answer or failed call may retry within the attempt limit.
- Rosa's controlled demo call uses `VOICE_DESTINATION_PHONE`.
- Patients with `.example.com` addresses use the local call simulator.
- A future non-demo case uses its own E.164 case phone.

All timing values are configurable through environment variables.

## Architecture

```text
FastAPI scheduler
      |
      | Twilio Calls API with inline TwiML
      v
configured destination phone
      |
      | fixed reminder is spoken, then the call hangs up
      v
signed Twilio status callback
      |
      v
persistent VoiceSession audit record

email reply -> existing parser -> solver -> clinician workflow
```

The scheduler runs inside the FastAPI process and can also be invoked from the
CLI with `make voice-due`. Atomic session creation prevents two scheduler
iterations from starting the same attempt.

## Persistent state

Each `VoiceSession` stores:

```text
id
case_id
missing_fact_id
provider
provider_contact_id
status
attempt
phone
language
message
created_at
updated_at
due_at
result
error
```

Supported statuses are `dialing`, `connected`, `completed`, `no_answer`,
`declined`, `cancelled`, `needs_human`, and `failed`. The last two remain in the
shared model for compatibility; the reminder flow does not generate a
transcript.

## API

### Health

```http
GET /api/voice/health
```

Returns provider readiness and scheduling policy without exposing credentials.

### Case reminder status

```http
GET /api/cases/{case_id}/voice
```

Also included in `GET /api/cases/{case_id}`. It reports the due time, eligibility
reason, attempt count, latest session, and fixed reminder message.

### Start now

```http
POST /api/cases/{case_id}/voice/call
```

Bypasses the initial waiting delay but still enforces case eligibility, attempt
limits, and the calling window for Twilio.

### Local completion

```http
POST /api/cases/{case_id}/voice/simulate
Content-Type: application/json

{"outcome": "completed"}
```

Useful outcomes are `completed`, `no_answer`, and `failed`.

### Twilio status callback

```http
POST /api/voice/twilio/status/{session_id}
X-Twilio-Signature: ...
```

The API validates Twilio's HMAC-SHA1 signature over the public callback URL and
form fields. `queued`, `ringing`, `in-progress`, `completed`, `busy`,
`no-answer`, `canceled`, and `failed` statuses map to the internal session
states. Repeated terminal callbacks are idempotent.

## Configuration

```dotenv
VOICE_BACKEND=twilio
VOICE_DESTINATION_PHONE=+1...
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_PHONE=+1...
LAPSE_PUBLIC_API_URL=https://api.example.com
```

The public API URL must use HTTPS and match the URL Twilio calls, because it is
part of signature validation.

## Acceptance criteria

- The local simulator requires no external account.
- A due case starts no more than one call attempt.
- Rosa's provider request uses `VOICE_DESTINATION_PHONE`.
- Dummy patient calls never reach Twilio.
- TwiML contains only the fixed language-appropriate reminder.
- A completed call leaves the case `waiting_patient`.
- A later email reply follows the existing parser and can advance the case.
- An email reply before the call prevents dialing.
- No-answer calls retry only after the configured interval.
- Calls stop at the attempt limit.
- Calls outside the configured window do not start.
- Invalid Twilio callback signatures are rejected.
- Duplicate callbacks do not duplicate events or facts.
