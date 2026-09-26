# Bedrock trust layer

## Goal

Add two independent Bedrock safety checks without changing the deterministic
eligibility engine or any UI:

1. Block patient-facing statements that claim someone is eligible, ineligible,
   approved, denied, or exempt.
2. Score whether each Channel B claim is grounded in its original note, then
   compare that result with the existing LLM verifier.

## Why contextual grounding is different

The existing verifier sees a structured claim and its quoted sentence, then
uses a separate model prompt to decide whether the sentence supports the claim.
Bedrock Contextual Grounding is a managed Guardrails filter. It receives:

- `grounding_source`: the complete clinical note;
- `query`: the evidence-extraction standard;
- `guard_content`: a normalized assertion about the patient.

Bedrock returns grounding and relevance scores plus an intervention decision.
It does not replace the verifier or decide eligibility.

The guarded content must be the normalized assertion, not merely the verbatim
quote. A sentence such as "Her mother has severe arthritis" is present in the
note and would trivially pass a quote-exists check. The normalized assertion
"The patient currently has severe arthritis" should fail because the note
attributes the condition to someone else.

## AWS resource

One Guardrail contains:

- a denied topic for patient-directed coverage and eligibility determinations;
- a contextual `GROUNDING` filter with threshold `0.70`;
- a contextual `RELEVANCE` filter with threshold `0.70`.

`scripts/provision_guardrail.py` creates or updates the named Guardrail, creates
an immutable version, and writes its non-secret ID, version, and region to
`.cache/bedrock_guardrail.json`. Environment variables override that local
file:

- `BEDROCK_GUARDRAIL_ID`
- `BEDROCK_GUARDRAIL_VERSION`
- `AWS_REGION`

The normal boto3 credential chain is used. The existing
`AWS_BEARER_TOKEN_BEDROCK` setup works for both the Bedrock control plane and
runtime.

## Runtime seams

`engine/guardrails.py` owns `ApplyGuardrail` calls and their disk cache.

- `check_patient_message(text)` returns the denied-topic assessment.
- `enforce_patient_message(text)` raises before a blocked message can be
  recorded or sent.
- `check_grounding(source, query, assertion)` returns both contextual scores.

The patient check is called by `engine/loop/outbound.py`. With no configured
Guardrail, local mode continues to use its fixed, reviewed templates. Once a
Guardrail is configured, a Bedrock error fails closed before the case changes.

## Grounding report

`python -m engine.grounding` evaluates every kept and dropped claim attached to
the current cases and writes `data/grounding.json`. Each row includes:

- claim, quote, note and normalized assertion;
- existing verifier decision;
- Bedrock grounding and relevance scores;
- Bedrock kept/dropped decision.

The summary reports:

- both kept;
- both dropped;
- verifier kept / Bedrock dropped;
- verifier dropped / Bedrock kept;
- agreement rate.

The report is the backend contract for a later `/eval` UI change. This build
does not modify `web/`.

## Cache and offline behavior

Guardrail responses are cached by Guardrail ID, immutable version, request
source and content under `.cache/guardrails/`. `LAPSE_OFFLINE=1` replays cached
assessments and refuses uncached AWS calls.

## Explicit non-goals

- No UI or API route changes.
- No change to `Claim`, `Fact`, or determination contracts.
- No use of grounding scores in the eligibility decision.
- No full-cohort rerun; the current synthetic case claims are sufficient for
  the demo comparison.
