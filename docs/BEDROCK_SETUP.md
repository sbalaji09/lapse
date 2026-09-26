# Bedrock local setup

Lapse uses one Amazon Bedrock API key for local development. No AWS CLI, IAM
profile, access-key pair, or deployed infrastructure is required.

## 1. Create the API key

In the Amazon Bedrock console, create a Bedrock API key in `us-east-1`. Copy it
when AWS shows it; it is the only secret used below.

## 2. Configure `.env`

```env
LAPSE_BACKEND=aws
AWS_BEARER_TOKEN_BEDROCK=your-bedrock-api-key
AWS_REGION=us-east-1

BEDROCK_MODEL_FAST=amazon.nova-lite-v1:0
BEDROCK_MODEL_VERIFY=amazon.nova-pro-v1:0
```

Keep the API key private and never commit `.env`.

## 3. Create the denied-topic Guardrail

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 -m scripts.provision_guardrail
```

The provision command creates or updates `lapse-patient-safety`, creates an
immutable version, and writes its non-secret ID and version to:

```text
.cache/bedrock_guardrail.json
```

You do not need to copy those values into `.env` on the same machine. For a
different machine, set `BEDROCK_GUARDRAIL_ID` and
`BEDROCK_GUARDRAIL_VERSION` from that file.

## 4. Run the app

```bash
make api
make web
```

Open `http://localhost:3000/cases/g-rosa`. The Guardrail challenge appears in
Rosa's “Next step” panel, beside the clinic worker's real outbound action.

Click **Attempt unsafe send**. Bedrock should return
`GUARDRAIL_INTERVENED`, identify **Patient eligibility determinations**, and
show the safe guidance. Rosa never sees the app or the blocked draft: the
clinic worker uses this screen, and the demonstration never sends a message or
changes her case.

On the queue, **Read the charts live** deliberately bypasses the LLM response
cache. It streams each patient from waiting to reading to ready as the evidence
finder and verifier complete real Bedrock calls. Completed patient charts can
be opened while the rest of the run continues.

## Optional command-line check

```bash
python3 -m engine.guardrails \
  "Rosa, you are exempt from the work requirement and will keep your Medi-Cal coverage."
```
