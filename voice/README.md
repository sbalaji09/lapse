# Automatic reminder calls

Lapse waits 24 hours after sending a patient email. If no email reply arrives,
it calls one configured phone number during the 09:00–18:00 Pacific calling
window and reads a fixed reminder:

> Hello, this is an automated reminder from your clinic. Please check your
> email and submit the information we requested. If you need help, call your
> clinic. Thank you.

Spanish cases receive the equivalent fixed Spanish reminder. The call does not
ask questions, record speech, write patient facts, or make an eligibility
decision. The person must reply to the existing email.

## Local test

The default backend is a simulator and needs no phone provider:

1. Run `make api` and `make web`.
2. Open Rosa's case and send the email.
3. Click **Call now**.
4. Click **Complete test reminder** or **Mark no answer**.
5. Confirm a completed reminder leaves the case waiting for an email reply.

To exercise automatic scheduling quickly:

```dotenv
VOICE_ESCALATION_MINUTES=1
VOICE_RETRY_MINUTES=1
VOICE_SCHEDULER_INTERVAL_SECONDS=5
VOICE_CALL_START_HOUR=0
VOICE_CALL_END_HOUR=24
```

Run `make voice-check` to inspect configuration and `make voice-due` to process
due calls immediately.

## Real test call with Twilio

1. Create a Twilio account and obtain a voice-capable Twilio phone number.
2. If the account is in trial mode, verify the destination phone in Twilio.
3. Expose the FastAPI server through a public HTTPS URL, using a deployed API,
   ngrok, or Cloudflare Tunnel.
4. Set:

   ```dotenv
   VOICE_BACKEND=twilio
   VOICE_AUTOMATION_ENABLED=1
   VOICE_DESTINATION_PHONE=+14086100377
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_FROM_PHONE=+1...
   LAPSE_PUBLIC_API_URL=https://your-public-api.example
   ```

5. Restart the API and run `make voice-check`.
6. Send Rosa's email, then click **Call now**.

`VOICE_DESTINATION_PHONE` controls Rosa's real demo call and is
`+14086100377`. Other `.example.com` demo patients use the local simulator;
their fabricated phone numbers are never dialed. Credentials stay in
environment variables and must not be committed.

Twilio receives inline TwiML containing the fixed reminder. It posts signed
status callbacks to the public API, allowing Lapse to distinguish a completed
call from no answer or failure.

## Default safeguards

- First call after 24 hours without an email reply.
- One retry after another 24 hours.
- Calls only from 09:00 to 18:00 in `America/Los_Angeles`.
- Any email reply stops automatic calls.
- A completed reminder stops additional calls but does not change the case.
- Calls left active without a callback become `no_answer` after 10 minutes.
- Provider errors redact the destination number.
