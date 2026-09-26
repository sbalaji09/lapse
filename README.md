# Lapse

## Run locally

```bash
make api
make web
```

The web app runs at `http://localhost:3000` and forwards `/api` requests to the
FastAPI server on port 8000.

## Demo email delivery

**Read the notes** performs analysis only and never sends an email. Open an
individual patient and click the email action to send manually. The app
persists whether that email was sent, prepared as a demo preview, or has not
been sent.

Every patient page has persistent **Send email** and **Call patient** controls.
For a patient-held missing fact, email asks the reviewed question and can be
intentionally resent. Other cases receive a neutral clinic reminder. Completed
calls remain in the history without removing the manual call control.

Rosa is routed to `siddharthbalaji6@gmail.com`; `.example.com` recipients are
recorded as demo previews and are never delivered. With a `RESEND_API_KEY` in
`.env`, Rosa's delivery uses Resend. The default sender is suitable for
Resend's test mode:

```dotenv
RESEND_API_KEY=re_...
RESEND_FROM_EMAIL=Lapse <onboarding@resend.dev>
```

Set `EMAIL_BACKEND=local` to return to preview-only mode. Tests always force
local mode and never send real messages.

Every rendered subject and body passes through the outbound guardrail before
the email provider or case state is touched. A deterministic local policy
always blocks eligibility, exemption, coverage-outcome, and internal-status
language. If Amazon Bedrock Guardrails is configured, it performs an
additional check and failures stop delivery.

## Demo reminder calls

Rosa's reminder call uses `+14086100377`. Dummy patients use the local call
simulator so fabricated phone numbers are never dialed. A real call still
requires Twilio:

```dotenv
VOICE_BACKEND=twilio
VOICE_DESTINATION_PHONE=+14086100377
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_PHONE=+1...
LAPSE_PUBLIC_API_URL=https://your-public-api.example
```

See [voice/README.md](voice/README.md) for local simulation and callback setup.
