# AWS - Moving Lapse onto AWS services (post-hackathon)

Read PROJECT.md first.
This is not hackathon work: PROJECT.md lists Lambda/Dynamo/S3/EventBridge under "Not doing", and that stands until the demo is recorded.
This file is the plan for when we move from a laptop demo to something a clinic or plan could run.
Do the sections in order; each one is shippable on its own.

## Principles
- **Swap at the seams, never the contracts.** The models in `engine/models.py`, the API table and the fact registry do not change.
  Every AWS service sits behind an existing module (`engine/llm.py`, `engine/store.py`, `engine/loop/`), so Track B's UI never notices.
- **Local mode stays first-class.** `LAPSE_BACKEND=local` (default) keeps SQLite, the disk LLM cache and simulated email, so the demo still replays offline.
  `LAPSE_BACKEND=aws` switches every seam at once.
  No code path exists only in AWS.
- **HIPAA-eligible services only.** We are synthetic today, but the whole point of the product is real patients later.
  Only use services on the AWS HIPAA Eligible Services list, and sign the AWS BAA (via AWS Artifact) before a single real record touches the account.
- **Boring over clever.** Containers on Fargate and a Postgres database, not a mesh of Lambdas.
  Lambda only where AWS hands us an event (inbound email).
- **One region.** `us-west-2` for everything (California program, Bedrock model availability).
  No cross-region data.
- **Infrastructure as code from day one.** AWS CDK in Python under `infra/`, same language as the engines.
  Nothing is click-created in the console except the BAA and the root account.

## Target architecture
```
                    Amplify Hosting (web/, Next.js)
                                |
                                v
   ALB (HTTPS, ACM cert) -> ECS Fargate: api/ (FastAPI) --------> RDS Postgres (store)
                                |    |                                 ^
                                |    +--> S3: attestations/ (PDFs) |   |
                                |    +--> Bedrock (LLM)            |   |
                                |    +--> SES (outbound email)     |   |
                                |                                  |   |
   SES inbound -> S3: inbound/ -> Lambda -> POST /api/inbound/email    |
                                                                       |
   EventBridge Scheduler (daily) -> ECS Fargate task: engine.pipeline -+
                                        +--> Bedrock batch inference (Channel B + verifier)
                                        +--> S3: llm-cache/, synthea/, external/

   Secrets Manager (keys, HMAC secret)   KMS (one CMK, all data at rest)   CloudWatch (logs, alarms)
```

## Service map
| Today (local) | On AWS | Seam in code | Why this service |
|---|---|---|---|
| OpenAI via `engine/llm.py` | Amazon Bedrock | `engine/llm.py`, `MODEL_FAST` / `MODEL_VERIFY` in `engine/config.py` | Already planned as "a config swap" in PROJECT.md; keeps inference inside the AWS BAA boundary |
| Disk LLM cache `.cache/llm/` | S3 `llm-cache/` prefix | `engine/llm.py` | Same content-hash keys; cache is shared across pipeline runs and machines |
| SQLite `lapse.sqlite` | RDS for PostgreSQL | `engine/store.py` | Case is a JSON aggregate; Postgres `jsonb` maps one-to-one and keeps SQL filtering for the queue |
| `data/synthea/`, `data/external/`, `data/truth/` | S3 `data/` prefix | paths in `engine/config.py` | Pipeline task reads inputs from S3 instead of the repo |
| reportlab PDF returned inline | S3 `attestations/` + presigned URL | `engine/loop/` PDF writer, `/cases/{id}/attestation.pdf` | Durable, versioned audit copy of every signed attestation |
| Resend outbound email | Amazon SES | `engine/loop/` email sender | HIPAA-eligible, same account and BAA |
| Resend inbound webhook | SES receipt rule -> S3 -> Lambda | `/api/inbound/email` (unchanged) | Lambda only forwards the raw message to the existing handler |
| `make api` on a laptop | ECS on Fargate behind an ALB | `api/Dockerfile` | Long-running FastAPI container, no cold starts, no server patching |
| `make web` on a laptop | AWS Amplify Hosting | `web/` | Native Next.js App Router hosting from the git repo |
| `make pipeline` by hand | EventBridge Scheduler -> Fargate task | `engine/pipeline.py` | Renewals are rolling; the queue must refresh daily without anyone pressing a button |
| `.env` | AWS Secrets Manager | `engine/config.py` settings loader | No keys in images or environment files |
| Voice call (stretch) | Amazon Connect + Polly + Transcribe | `engine/loop/` voice | Outbound call in the patient's language; transcript lands as a `patient_reply` fact |

---

## AWS0 - Account, BAA and guardrails

### Goal
A clean account that is safe to put real data in later, before any service is used.

### Do
1.
   Create a dedicated AWS Organization with two accounts: `lapse-dev` and `lapse-prod`.
   Never share one account between them.
2.
   Accept the AWS BAA in AWS Artifact for both accounts.
3.
   Enable CloudTrail (all regions, log file validation on) to an S3 bucket in a separate log-archive account or at least a locked bucket.
4.
   Enable AWS Config with the "Operational Best Practices for HIPAA Security" conformance pack, and GuardDuty.
5.
   One customer managed KMS key per account for all data at rest (S3, RDS, Secrets Manager, CloudWatch Logs).
6.
   IAM Identity Center for humans; no long-lived IAM user access keys.
7.
   Budget alarm in AWS Budgets (email both of us at 50%, 80%, 100% of monthly budget).

### Done when
`aws sts get-caller-identity` works through Identity Center for both of us, the BAA shows as accepted, and the HIPAA conformance pack reports.

---

## AWS1 - Infrastructure skeleton (CDK)

### Goal
`cdk deploy` brings up networking, storage and secrets with nothing running yet.

### Do
1.
   `infra/` CDK app in Python, one stack per concern: `NetworkStack`, `DataStack`, `ComputeStack`, `EmailStack`, `WebStack`.
2.
   `NetworkStack`: VPC with private subnets for compute and data, public subnets only for the ALB, VPC endpoints for S3, Bedrock runtime, Secrets Manager and ECR so traffic stays off the public internet.
3.
   `DataStack`: one S3 bucket `lapse-<env>` with prefixes `data/`, `llm-cache/`, `attestations/`, `inbound/`; block public access, KMS encryption, versioning on, Object Lock (governance mode) on `attestations/`.
4.
   `DataStack`: RDS for PostgreSQL, Multi-AZ in prod only, encrypted with the CMK, in private subnets, IAM auth enabled, automated backups 14 days.
5.
   Secrets Manager entries: `lapse/<env>/clinician-hmac`, `lapse/<env>/db`.
   No OpenAI or Resend keys once their replacements land.
6.
   Add `LAPSE_BACKEND`, `AWS_REGION`, `LAPSE_BUCKET`, `DATABASE_URL` to `.env.example`, commented as AWS-only.

### Done when
`cd infra && cdk deploy --all -c env=dev` succeeds from a clean checkout and `cdk diff` is empty on a second run.

---

## AWS2 - Bedrock for LLM calls

### Goal
Channel B, the verifier and reply parsing run on Bedrock with no change to their callers.

### Do
1.
   In `engine/llm.py`, add a Bedrock client (boto3 `bedrock-runtime`, Converse API) alongside the OpenAI one, chosen by `LAPSE_BACKEND`.
2.
   Keep two model settings in `engine/config.py`: a fast model for extraction and parsing, a stronger one for the verifier.
   Take exact model IDs from the Bedrock console for `us-west-2` at the time; do not hard-code them anywhere but config.
3.
   Request model access in the Bedrock console for both models in both accounts.
4.
   Move the disk cache to S3 under `llm-cache/<sha256 of model + prompt + params>.json`.
   Local mode keeps `.cache/llm/`.
   The cache key must include the model ID so a model swap never serves stale answers.
5.
   For the full-cohort pipeline run, use Bedrock batch inference (JSONL in S3 in, JSONL out) instead of 1,000+ live calls; it is cheaper and not rate limited.
   Live calls stay for the patient loop.
6.
   Re-run the eval (`engine/eval.py`) on Bedrock and record precision/recall next to the OpenAI numbers.
   The switch only ships if the verifier's kept/dropped counts and claim-level F1 are no worse.

### Done when
`LAPSE_BACKEND=aws python -m engine.pipeline` produces the same bucket counts on the golden seven as local mode, and the eval page shows the Bedrock run.

---

## AWS3 - Postgres store and S3 data

### Goal
`engine/store.py` works on RDS with the same public functions, and the pipeline reads inputs from S3.

### Do
1.
   Mirror the SQLite schema in Postgres, with `data` as `jsonb`.
   Keep the indexed columns (`renewal_date`, `bucket`, `fragile`, `status`, `clinic_id`) as real columns.
2.
   Use plain SQL migrations under `infra/migrations/` run by the pipeline task on start (no ORM; the store is small and already hand-written).
3.
   `store.py` picks SQLite or Postgres from `DATABASE_URL`; every existing test in `tests/` must pass against both.
   Add a CI job that runs them against a Postgres service container.
4.
   Upload `data/synthea/fhir/`, `data/external/`, `data/truth/` to `s3://lapse-dev/data/`.
   Paths in `engine/config.py` resolve to S3 in AWS mode.
5.
   `/demo/reset` stays and works against Postgres, but is disabled in prod.

### Done when
All tests pass against both backends and the API serves the queue from RDS.

---

## AWS4 - Run the API and web

### Goal
A public HTTPS URL for the web app and API in `lapse-dev`.

### Do
1.
   `api/Dockerfile` (python:3.11-slim, non-root user, uvicorn).
   Push to ECR from CI.
2.
   ECS service on Fargate, 2 tasks in private subnets, behind an ALB with an ACM certificate.
   Task role grants only: its S3 prefixes, Bedrock invoke on the two model IDs, SES send, its secrets.
3.
   Tighten CORS in `api/main.py` from `localhost:3000` to the Amplify domain, read from config.
4.
   Amplify Hosting for `web/`, connected to the repo, `NEXT_PUBLIC_API_URL` set to the ALB domain.
5.
   Health check on `/api/health`; CloudWatch alarm on 5xx rate and unhealthy targets.
6.
   There is still no auth in the product.
   Until there is, put the dev environment behind Amplify basic auth and an ALB rule restricting source IPs.
   Real auth (Cognito for clinic staff) is its own project and must land before AWS6.

### Done when
The queue, case detail and clinician card all work at the Amplify URL against the ALB.

---

## AWS5 - Email loop on SES, PDFs on S3

### Goal
Rosa's loop (question out, reply in, case flips, clinician signs, PDF out) runs entirely on AWS.

### Do
1.
   Verify a sending domain in SES with DKIM, SPF and a DMARC record.
   Request production access (out of the sandbox) for `lapse-prod` only.
2.
   Replace the Resend sender in `engine/loop/` with SES `SendEmail`, keeping the same HTML template and `message_id` handling.
3.
   Inbound: MX record for a reply subdomain -> SES receipt rule -> store raw message in `s3://.../inbound/` -> Lambda parses sender, `In-Reply-To` and body -> `POST /api/inbound/email`.
   The Lambda holds no business logic; parsing into a typed fact stays in `engine/loop/`.
4.
   Signed attestation PDFs are written to `attestations/<patient_id>/<attestation_id>.pdf`; `/cases/{id}/attestation.pdf` returns a 5-minute presigned URL redirect.
   Object Lock keeps signed copies immutable for the retention period.
5.
   SES configuration set with bounce and complaint events to CloudWatch; a bounced patient email marks the MissingFact as unreachable and surfaces in the queue.

### Done when
A reply from a real test inbox flips `g-rosa` from ONE_AWAY and the signed PDF is downloadable from S3.

---

## AWS6 - Scheduled pipeline for rolling renewals

### Goal
The queue refreshes itself daily, because renewals are rolling and the 30-day window moves every day.

### Do
1.
   Package `engine/` as a second image (or a second entrypoint of the same image) that runs `python -m engine.pipeline`.
2.
   EventBridge Scheduler triggers it as a Fargate task every day at 04:00 Pacific.
3.
   `AS_OF_DATE` becomes "today" in prod only, still overridable by env var so dev and tests stay pinned to 2027-02-15.
4.
   The run is idempotent: it re-determines every case, preserves facts, events and open questions, and only appends events for real changes.
5.
   CloudWatch alarm if the task fails or takes longer than 2x its usual duration.

### Done when
Two consecutive scheduled runs complete, and a case whose renewal enters the window appears in the queue without anyone touching it.

---

## AWS7 - Voice for non-responders (stretch)

### Goal
The existing voice stretch, on AWS: a patient who does not answer the email gets one call in their language.

### Do
1.
   Amazon Connect instance with an outbound contact flow; Polly voices for `en` and `es` (and any other language we support in email).
2.
   The flow asks the same single question from the MissingFact and records the answer; Transcribe turns it into text.
3.
   The transcript goes to the same reply handler as email, stored as a `patient_reply` fact with the recording's S3 key in `source_ref`.
4.
   Same refusals as email: never states eligibility, never uses legal or medical language.

### Done when
A call to a test phone number resolves `standing_tolerance_minutes` for a copy of Rosa.

---

## Cost notes (dev, rough)
- Fixed monthly floor is the ALB, RDS (single-AZ small instance in dev) and NAT or VPC endpoints; expect this to dominate while traffic is tiny.
- Bedrock cost scales with cohort size times notes per patient; batch inference and the S3 cache mean an unchanged patient costs nothing on re-run.
- Tear dev down with `cdk destroy` when not in use; the S3 bucket and RDS snapshots are retained by policy.

## Open questions (decide before AWS1)
- Postgres on RDS versus Aurora Serverless v2: RDS is the default here; revisit only if load is spiky enough to justify Aurora.
- Does each clinic get its own deployment (data isolation by account) or one multi-tenant deployment with `clinic_id` scoping? This changes AWS0 and the auth design.
- Which states' rule packs ship first, and does any state require data to stay in a specific region?
