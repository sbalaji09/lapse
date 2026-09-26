# Bedrock patient-safety Guardrail

## Goal

Use Amazon Bedrock Guardrails to enforce one product rule: Lapse may explain a
review or ask a patient for information, but it must never tell a patient that
they are eligible, ineligible, approved, denied, or exempt.

The deterministic eligibility engine is unchanged. The Guardrail protects
patient-facing language only.

## AWS resource

`python -m scripts.provision_guardrail` creates or updates one denied-topic
Guardrail named `lapse-patient-safety`, creates an immutable version, and saves
its non-secret ID, version, and region to `.cache/bedrock_guardrail.json`.

Environment variables override that local configuration:

- `BEDROCK_GUARDRAIL_ID`
- `BEDROCK_GUARDRAIL_VERSION`
- `AWS_REGION`

For local development, set only `AWS_BEARER_TOKEN_BEDROCK`. boto3 discovers
that Bedrock API key automatically; no IAM profile or AWS access-key pair is
needed.

The denied topic covers direct statements to a patient that decide or promise:

- eligibility or approval;
- denial or ineligibility;
- an exemption determination;
- guaranteed continuation or loss of coverage.

Neutral process explanations and requests for information remain allowed.

## Runtime seam

`engine/guardrails.py` owns the `ApplyGuardrail` request and disk cache.

- `check_patient_message(text)` returns Bedrock's action, safe output and topic
  assessment.
- `enforce_patient_message(text)` raises `GuardrailIntervened` when Bedrock
  blocks the candidate.

`engine/loop/outbound.py` applies the check before recording or sending a
patient message. With no configured Guardrail, local mode continues to use its
fixed reviewed templates. Once configured, AWS errors fail closed before the
case changes.

Guardrail responses are cached by immutable Guardrail version and candidate
text under `.cache/guardrails/`. `LAPSE_OFFLINE=1` replays cached assessments
and refuses uncached AWS calls.

## Demo API and UI

`POST /api/guardrails/check` accepts a candidate message and returns the real
Bedrock assessment. Rosa's case page presents one fixed synthetic attempt at
the clinic worker's outbound-message boundary:

> Rosa, you are exempt from the work requirement and will keep your Medi-Cal
> coverage.

The expected response is `GUARDRAIL_INTERVENED`, the detected denied topic, and
the configured safe guidance. Rosa never sees the app or the blocked text. The
interaction is deliberately separate from the eligibility engine: it
demonstrates enforcement without changing a case or sending a message, while
the reviewed “Email Rosa the question” flow remains available.

## Explicit non-goals

- No contextual grounding.
- No use of a model or Guardrail in the deterministic eligibility decision.
- No new `Claim`, `Fact`, or determination fields.
- No patient message is actually sent by the demo interaction.
