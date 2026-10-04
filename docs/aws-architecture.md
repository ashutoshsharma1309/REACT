# REACT Merchant on AWS

This document describes how REACT Merchant runs locally and on AWS, why each AWS service is there, and what has and has not been verified. The AWS design was built in Phase 6 and is unchanged by the merchant pivot, except that the task now runs the merchant domain (`REACT_DOMAIN=merchant`) and seeds the synthetic Astra Retail business.

## 0. Status: read this first

| Label | Meaning |
|---|---|
| **IMPLEMENTED** | Application code and infrastructure code exist in this repository and pass automated tests offline. |
| **DEPLOYED** | Running in an AWS account. **Nothing is deployed yet.** The build machine had no AWS account, credentials, AWS CLI or Docker. |
| **PROPOSED / FUTURE** | Evaluated and documented, deliberately not built. |

What was verified, and how:

| Area | Verified by | Not verified |
|---|---|---|
| Bedrock adapter (Converse API) | botocore Stubber tests validate request and response shapes against the real service model | No live Bedrock call has been made |
| S3 artifact store | Stubber tests (put, get, encryption header, error sanitising) | No live S3 call |
| Infrastructure (CDK) | `cdk synth` plus 12 assertion tests on the generated CloudFormation | Not deployed |
| Container image | Locked dependencies resolve for Linux x86_64 with matching hashes; the full suite passes on Python 3.12 with those exact versions | `docker build` has not been run (no Docker on the build machine); the CI job does it |
| CI workflows | actionlint passes | Never run on GitHub (the repository is not under git yet) |
| Deploy workflow and script | shell syntax check and actionlint | Never run |

Do not describe REACT as "running on AWS" until the deploy script has run and `/api/ready` answers from CloudFront.

## 1. Local architecture (unchanged, still the default)

```
Browser ─► Vite dev server :5180 ──/api proxy──► FastAPI :8000 ─► PostgreSQL (local)
                                                   │
                                                   ├─ deterministic provider (default, no LLM, no AWS)
                                                   ├─ evidence packs ─► ./var/artifacts (local folder)
                                                   └─ JSON logs on stdout (EMF metrics off)
```

* `LLM_PROVIDER=deterministic` (default) needs no credentials or network. `mock` is for tests. `bedrock` is optional locally and uses your normal AWS credential chain, for example `AWS_PROFILE`.
* `docker compose up --build` runs the same image that ECS runs, against a local PostgreSQL container.
* Normal tests need no AWS credentials: `pytest` (backend), `npm test` and `npm run e2e` (frontend), `npm test` (infra).

## 2. AWS architecture (IMPLEMENTED in code, not deployed)

```
USER (browser)
  │ HTTPS
  ▼
FRONTEND ─ Amazon CloudFront ─┬─ /*      ─► Amazon S3 "site" bucket (private, Origin Access Control)   React SPA
                              │
API                           └─ /api/*  ─► Application Load Balancer (reachable only from CloudFront)
  │                                            │
  ▼                                            ▼
REACT CORE                     Amazon ECS on AWS Fargate: FastAPI container (same image as local)
  │                            detection · investigation · impact engine · decision engine · approvals
  ▼
AGENTS                         Orchestrator + Detection / Investigation / Evidence / Decision agents
  │                            (application orchestration, in-process; limits, trace, fallback)
  ▼
TOOLS                          Registered read-only tools over the database (no SQL, code, HTTP or files)
  │
  ▼
DATABASE / STORAGE             Amazon RDS for PostgreSQL (isolated subnets)  ◄─ credentials: AWS Secrets Manager
                               Amazon S3 "artifacts" bucket: evidence packs (versioned, encrypted)
  │
  ▼
AWS AI (optional)              Amazon Bedrock, Converse API, one configured model
                               (only when deployed with llmProvider=bedrock; default is rule-based)
  │
  ▼
OBSERVABILITY                  Amazon CloudWatch: Logs (30-day retention), EMF metrics, dashboard, 7 alarms
                               optional: Amazon SNS e-mail + AWS Budgets (only when an alarm e-mail is given)

Images: built from the repository Dockerfile by CDK and pushed to Amazon ECR (the CDK bootstrap repository).
Deploy-time only: one AWS Lambda function run by CloudFormation to look up the CloudFront origin prefix list.

PROPOSED / FUTURE (not in the stack): AWS Step Functions · Amazon Bedrock AgentCore · Amazon SQS · Amazon ElastiCache ·
Amazon Aurora · Amazon Cognito · AWS WAF · document ingestion (S3 upload → Textract) · NAT gateway / VPC endpoints
```

Infrastructure code: [infra/lib/react-stack.ts](../infra/lib/react-stack.ts). One stack per environment: `React-staging` (default) or `React-production`.

## 3. AWS services: what, why, where, how

### 3.1 IMPLEMENTED

| Service | What it does in REACT | Why it is needed | Where (code) | How | Problem it solves |
|---|---|---|---|---|---|
| **Amazon ECS on AWS Fargate** | Runs the FastAPI API container | Agent runs take seconds and hold a DB pool; a long-lived container fits better than per-request functions | `infra/lib/react-stack.ts` (`ApiTask`, `Api`) | 0.5 vCPU / 1 GiB tasks, 1 by default, autoscaling on CPU to a hard ceiling of 3 | Runs the unchanged app without managing servers |
| **Elastic Load Balancing (ALB)** | Health-checked entry point to the tasks | Rolling deploys, health checks, several tasks | same | `/health` target health check; security group admits only the CloudFront origin-facing prefix list | Zero-downtime deploys; unhealthy tasks are replaced |
| **Amazon CloudFront** | One HTTPS origin for UI and API | The SPA and the API share an origin, so no CORS and no backend URL in the bundle | same (`Cdn`, `SpaRewrite`) | `/api/*` goes to the ALB with caching disabled; everything else goes to S3. A CloudFront Function serves `index.html` for client routes | TLS for users, edge delivery (India edge locations included), a private S3 site bucket |
| **Amazon S3** (2 buckets) | *site*: built UI. *artifacts*: evidence packs written on approve or reject | Durable, cheap, encrypted object storage | `app/storage/s3.py`, `app/services/artifact_service.py`, stack | Block Public Access, TLS-only policy, SSE-S3, versioning on artifacts, 90-day noncurrent expiry | Audit copies that outlive the database row; static hosting without servers |
| **Amazon RDS for PostgreSQL** | The REACT database (39 tables, 5 Alembic migrations) | The whole app is SQLAlchemy + Alembic on PostgreSQL; RDS keeps that unchanged | stack (`Database`), `app/config/settings.py` (`DB_*`) | PostgreSQL 16, db.t4g.micro, gp3 20 GiB growing to 50 GiB, encrypted, 7-day backups, isolated subnets | Managed backups, patching and failover with no schema change |
| **AWS Secrets Manager** | Holds the generated database username and password | Credentials must never be in git, the image or task environment in plain text | stack (`Credentials.fromGeneratedSecret`) | ECS injects `DB_USER` / `DB_PASSWORD` from the secret at start; the app composes the URL in memory | No database password anywhere in the repository or image |
| **Amazon ECR** | Stores the API image | ECS pulls images from a registry | CDK `ContainerImage.fromAsset` (bootstrap repository) | `cdk deploy` builds the Dockerfile and pushes it | Reproducible, versioned images |
| **Amazon CloudWatch** | Logs, application metrics, dashboard, alarms | Operational visibility without extra agents | `app/observability/metrics.py`, `app/observability/logging.py`, stack | awslogs driver (30-day retention). EMF lines on stdout become metrics with no API call and no extra IAM permission | See failures, AI usage and cost drivers; alarms for errors |
| **AWS IAM** | Task role, execution role, deploy role (documented) | Least privilege for each actor | stack, `deploy.yml` | Task role: `s3:PutObject` and `s3:GetObject` on `evidence-packs/*`, plus `bedrock:InvokeModel` on one model when enabled | The app can do only what it needs |
| **Amazon VPC** | Network isolation | RDS must not be reachable from the internet | stack (`Vpc`) | 2 AZs; public subnets for the ALB and tasks, isolated subnets for RDS; free S3 gateway endpoint; no NAT | Private database without NAT gateway cost |
| **Amazon Bedrock** *(optional mode)* | LLM for agent structured outputs, tool selection and explanations | Managed model access with IAM, no API keys | `app/agents/provider.py` (`BedrockProvider`) | Converse API via boto3 behind `LLMProvider`; model from `LLM_MODEL` / `-c bedrockModelId=` | AI reasoning without handling model keys. **Adapter implemented and Stubber-tested; never called live.** Default deployment is rule-based. |
| **Amazon SNS + AWS Budgets** *(only with `alarmEmail`)* | E-mail for alarms; monthly cost alerts at 50%, 80% and forecast 100% | A hackathon account must not surprise anyone with a bill | stack | Created only when an e-mail is supplied | Early warning on cost and failures |

### 3.2 PROPOSED / FUTURE (evaluated, not built)

| Service | Would do | Why not now |
|---|---|---|
| **AWS Step Functions** | Durable workflow across investigate, decide, wait for approval (task token), execute | The in-process orchestrator already owns state, limits, retries and the trace; a run finishes in under a second (rule-based) or tens of seconds (LLM). Approval is a stored state, not a waiting process. Two orchestrators would disagree. See section 4.3. |
| **Amazon Bedrock AgentCore** | Managed agent runtime, isolation, tool gateway, agent observability | Tools are in-process Python functions over the database with an allow-list per agent. AgentCore would add a network hop and a second trace system without solving a current problem. Mapping in section 4.2. |
| **Amazon SQS + ECS worker** | Queue investigations so API requests return at once | Needed when runs exceed the CloudFront 60-second origin timeout or when many businesses scan at once. Today `AGENT_MAX_EXECUTION_SECONDS=50` keeps runs inside it. |
| **Amazon ElastiCache (Redis)** | Cache, rate limiting, shared transient state | Nothing needs it: the API is stateless, the LLM budget is counted in PostgreSQL, and no cache is required at this load. `REDIS_URL` stays unused. |
| **Amazon Aurora PostgreSQL** | Faster failover, read replicas, Serverless v2 | More than a single-business demo needs. Moving later is a snapshot restore with no application change. See 4.4. |
| **Amazon Cognito** | Sign-in, reviewer identity for approvals | Authentication is outside Phase 6. The reviewer name is free text today (see Security). |
| **AWS WAF** | Rate limiting and managed rules at CloudFront | Worth adding once the URL is shared beyond judges; costs money per rule. |
| **Document ingestion** (S3 upload, Textract, parsing) | Let a business upload CSV, Excel or PDF | **Not implemented.** The UI shows it as *Coming soon*. The artifacts bucket and `app/storage` are the landing zone it would use. |
| **NAT gateway or interface VPC endpoints** | Move tasks to private subnets | NAT costs about $32 per month plus data. Tasks sit in public subnets with a public IP, and their security group admits only the ALB. |

## 4. Key decisions

### 4.1 Bedrock through the existing provider abstraction

`LLMProvider` stays the only door to a model: `DeterministicProvider`, `MockLLMProvider`, `BedrockProvider` and the new `UnavailableProvider`. Agents never import boto3. The Bedrock adapter uses the **Converse** API through boto3, which already existed and needs one IAM action, `bedrock:InvokeModel`.

* **Model ID** has no default in code. It comes from `LLM_MODEL` (CDK context `bedrockModelId`). Use a model or inference-profile ID that is enabled in your account and region; the Bedrock console lists them. If `LLM_PROVIDER=bedrock` and the ID is missing, REACT treats the model as unavailable instead of guessing.
* The Anthropic SDK's Bedrock client (`AnthropicBedrockMantle`) is the alternative for Claude on Bedrock and follows the Messages API more closely. Switching would be a new `LLMProvider` subclass and no change to any agent. It was not done because the Converse adapter works, is tested, and a rewrite was out of scope.
* The LLM never produces money. Revenue, profit, inventory value, production loss, opportunity value and downtime cost come from `app/impact/engine.py`. Decisions and evidence packs copy those figures, and validation rejects any change to the deterministic Evidence Support Score.

### 4.2 AgentCore: not used, how it would map

| REACT today | AgentCore equivalent |
|---|---|
| `Orchestrator` state machine | AgentCore Runtime session |
| `ToolExecutor` + per-agent allow-list (`app/agents/policies.py`) | AgentCore Gateway with tool targets |
| `AgentLimits` / `LimitTracker` | Runtime limits and timeouts |
| `agent_trace_entries`, `agent_tool_calls` | AgentCore Observability traces |

The tool functions and the `LLMProvider` boundary make this a later move if isolation or multi-tenant agent hosting becomes a requirement.

### 4.3 Application orchestration versus cloud workflow orchestration

* **Application orchestration (implemented):** the `Orchestrator` and `DecisionEngine` decide *what* REACT does: state transitions, which agent runs, tool permissions, retries (at most 1 per step), limits, fallback and the trace. The state lives in PostgreSQL (`agent_runs`, `decisions`, `approvals`, `action_executions`).
* **Cloud workflow orchestration (proposed):** Step Functions would decide *when and where* long steps run: queueing, waiting days for a human approval with a task token, retries across process restarts. It would call the same application functions and not replace them.

### 4.4 RDS for PostgreSQL rather than Aurora

The schema, SQLAlchemy models and 5 Alembic migrations stay as they are. A single-business demo needs about 1 GB of data and a handful of connections. db.t4g.micro costs roughly a quarter of the smallest always-on Aurora configuration. Production props turn on Multi-AZ, a larger class, 14-day backups and deletion protection. Moving to Aurora is a snapshot restore with no code change.

### 4.5 ECS Fargate rather than Lambda, App Runner or EC2

* **Lambda:** investigations with an LLM can take tens of seconds, the app keeps a SQLAlchemy pool, and first-boot seeding takes several seconds. Cold starts plus connection churn against RDS would need RDS Proxy.
* **App Runner:** simpler, but it gives less control over health checks, VPC networking and autoscaling ceilings.
* **EC2:** patching and capacity management with no benefit here.
* **Fargate** runs the same container as `docker compose`, has health checks, and deploys with circuit-breaker rollback.

### 4.6 S3 and CloudFront for the frontend

The UI is a static Vite build with no server-side rendering. S3 behind CloudFront with Origin Access Control is the smallest correct option. Amplify Hosting would add a second deploy system.

### 4.7 One IaC framework: AWS CDK (TypeScript)

The team already writes TypeScript for the UI, and CDK produces least-privilege grants and tested templates in code. Terraform and hand-written CloudFormation were not used, so there is exactly one IaC framework.

### 4.8 Secrets Manager rather than Parameter Store

RDS generates and stores the database secret directly, and it supports rotation. Nothing else in REACT is secret: there are no model API keys because Bedrock uses IAM. Non-secret settings are plain environment variables in the task definition.

## 5. Environment variables

| Variable | Local default | AWS (set by CDK) | Notes |
|---|---|---|---|
| `APP_ENV` | `development` | `staging` / `production` | Dimension on every metric |
| `DATABASE_URL` | local PostgreSQL | not set | Used when `DB_HOST` is empty |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_SSLMODE` | unset | RDS endpoint, `react`, `require` | When `DB_HOST` is set the URL is composed; the password is URL-encoded |
| `DB_USER`, `DB_PASSWORD` | unset | **injected from Secrets Manager** | Never in git, the image or plain task environment |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | 5, 5 | 5, 5 | Per worker; see Database |
| `AUTO_CREATE_SCHEMA` | `true` | `false` | AWS uses Alembic only |
| `RUN_MIGRATIONS` | container: `true` | `true` | `alembic upgrade head` at start, under an advisory lock |
| `SEED_ON_STARTUP`, `HISTORY_DAYS`, `SEED` | `true`, 120, 42 | `true` | Synthetic dataset, seeded once under an advisory lock |
| `REACT_DOMAIN` | `merchant` | `merchant` | Which domain pack a fresh seed generates (`merchant` = Astra Retail; `manufacturing` = backend reference pack). An existing dataset keeps its own domain (`system_state.domain`) |
| `WEB_CONCURRENCY` | 2 (container) | 2 | uvicorn workers per task |
| `STORAGE_BACKEND` | `local` | `s3` | |
| `STORAGE_LOCAL_DIR` | `var/artifacts` | n/a | Git-ignored |
| `ARTIFACTS_BUCKET`, `ARTIFACTS_PREFIX` | unset, `evidence-packs/` | bucket name, `evidence-packs/` | IAM is scoped to this prefix |
| `METRICS_ENABLED`, `METRICS_NAMESPACE` | `false`, `REACT` | `true`, `REACT` | EMF on stdout |
| `API_PATH_PREFIX` | `/api` | `/api` | CloudFront forwards `/api/*` unchanged |
| `CORS_ORIGINS` | Vite origins | empty | Same origin on AWS |
| `LLM_PROVIDER` | `deterministic` | `deterministic` or `bedrock` | also `mock` (tests), `unavailable` (drill) |
| `LLM_MODEL` | empty | CDK `bedrockModelId` | No default model ID anywhere in code |
| `AWS_REGION` | `us-east-1` | stack region | Credentials always from the standard chain |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_MAX_TOKENS`, `LLM_TEMPERATURE` | 30, 1, 2048, 0 | 20, 1, 2048, 0 | |
| `AGENT_MAX_ITERATIONS`, `AGENT_MAX_TOOL_CALLS`, `AGENT_MAX_LLM_CALLS` | 4, 12, 10 | same | Hard limits per run |
| `AGENT_MAX_EXECUTION_SECONDS` | 120 | 50 | Below the CloudFront 60 s origin timeout |
| `AGENT_FALLBACK_TO_DETERMINISTIC` | `true` | `true` | `false` makes AI failures return 503 |
| `LLM_HOURLY_CALL_BUDGET` | 200 | 200 (CDK `llmHourlyCallBudget`) | Across all tasks, counted in the database |
| `DEMO_CONTROLS_ENABLED` | `true` | staging `true`, production `false` | Gates `POST /demo/ai-outage` |

## 6. Deployment instructions

**Prerequisites:** an AWS account, credentials through SSO or a profile (never access keys in files), the AWS CLI v2, Docker, Node 22, and model access in the Bedrock console if you want Bedrock.

```bash
# 1. First time per account and region (creates the CDK bootstrap bucket, ECR repository and roles)
cd infra && npm ci && npx cdk bootstrap

# 2. See what will be created (no changes)
npx cdk diff -c envName=staging

# 3. Deploy everything: stack, image build and push, UI upload, invalidation, readiness smoke test
cd .. && scripts/deploy_aws.sh
#    with Bedrock:     LLM_PROVIDER=bedrock BEDROCK_MODEL_ID=<id enabled in your account> scripts/deploy_aws.sh
#    with alerts:      ALARM_EMAIL=you@example.com MONTHLY_BUDGET_USD=50 scripts/deploy_aws.sh

# 4. Open the AppUrl output. /api/ready must answer "ready".
```

* First boot runs migrations and seeds the synthetic dataset (120 days). The ALB grace period is 180 seconds.
* **Updates:** run `scripts/deploy_aws.sh` again. ECS does a rolling deployment and rolls back automatically if new tasks fail health checks.
* **From GitHub:** `.github/workflows/deploy.yml` (manual trigger) runs the same script after assuming an IAM role through OIDC. It needs the repository variables `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, and optionally `BEDROCK_MODEL_ID` and `ALARM_EMAIL`. Not run yet.
* **Teardown after the event:** `cd infra && npx cdk destroy React-staging`. Both S3 buckets are retained on purpose, because evidence packs are audit records. Empty and delete them by hand if you no longer need them. RDS leaves a final snapshot in staging.

## 7. IAM requirements

| Principal | Permissions | Scope |
|---|---|---|
| **API task role** (application) | `s3:PutObject`, `s3:GetObject` | `arn:…:<artifacts-bucket>/evidence-packs/*` only |
| | `bedrock:InvokeModel` (only when `llmProvider=bedrock`) | The configured foundation model in any region, plus the inference profile of that ID in this account and region. No wildcard models. |
| **Task execution role** (ECS agent) | ECR pull, CloudWatch Logs write, `secretsmanager:GetSecretValue` | The CDK asset repository, the REACT log group, the one database secret |
| **Frontend / browser** | none | The browser gets no AWS credentials. S3 is reachable only through CloudFront OAC. |
| **Database** | network only | Security group allows 5432 from the API task security group only; no IAM database access |
| **Observability** | none extra | EMF lines go through the log driver, so no `cloudwatch:PutMetricData` |
| **Deploy role** (GitHub OIDC, created outside this repo) | `sts:AssumeRole` on the CDK bootstrap roles, `s3:PutObject/DeleteObject/ListBucket` on the site bucket, `cloudfront:CreateInvalidation` on the distribution | Trusted only for this repository's `staging` / `production` environments |
| **Custom resource role** (deploy time) | `ec2:DescribeManagedPrefixLists` | Read-only; this action has no resource-level scoping |

No role has `AdministratorAccess` or any `*` action. The infra tests fail if one appears.

## 8. Database setup

* RDS PostgreSQL 16 in isolated subnets, encrypted, not publicly accessible. The secret is generated at deploy time.
* **Migrations:** the container runs `alembic upgrade head` before uvicorn starts. Alembic takes a PostgreSQL advisory lock (`app/database/locks.py`), so tasks starting together migrate one at a time. `AUTO_CREATE_SCHEMA=false` on AWS: Alembic is the only schema authority. `/ready` reports `BEHIND` if the database is not at the head revision.
* **Phase 6 migration** `b6e1f0c2d9a7`: adds the `artifacts` table and `system_state.ai_outage_mode`. It is additive and reversible.
* **Merchant migration** `c7d3e9a1f204`: adds the merchant tables (stores, product categories, purchase orders and items, supplier deliveries, pricing history, discounts, expenses), nullable merchant columns on existing tables, and `system_state.domain` / `profile`. It is additive and reversible (`alembic downgrade -1`); nothing is dropped or renamed.
* **Seeding:** the first task seeds the synthetic Astra Retail dataset (about 3 s) under a second advisory lock. The others wait, then see it is done.
* **Connections:** tasks × `WEB_CONCURRENCY` × (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`), which is 3 × 2 × 10 = 60 at the autoscaling ceiling. That stays under db.t4g.micro's limit of about 80. Raise the instance class before raising `maxCount`.
* **Local database moved under Alembic:** a database created earlier by `AUTO_CREATE_SCHEMA` can be adopted with `alembic stamp a8d2ea2a985c && alembic upgrade head`.

## 9. S3 setup

* **Site bucket:** the built UI. It is private and CloudFront reads it through Origin Access Control. `scripts/deploy_aws.sh` syncs `frontend/dist`: hashed assets are cached for a year and `index.html` is never cached.
* **Artifacts bucket:** evidence packs, one JSON object per approve or reject decision, stored at `evidence-packs/YYYY/MM/DD/<decision_id>/<artifact_id>.json`.
  * **Contents:** the decision, the approval and simulated action, the investigation conclusion, the cited evidence with source record IDs, the impact-engine figures (copied, not recalculated), the visible agent trace, and provenance (synthetic data, seed, generator version, data window).
  * **Integrity:** the SHA-256 is stored on the `artifacts` row and in the object metadata. `GET /artifacts/{id}/download` re-checks it and refuses tampered bytes.
  * **Protection:** Block Public Access, TLS-only bucket policy, SSE-S3 encryption, versioning, noncurrent versions expire after 90 days, retained when the stack is deleted.
  * **Failure:** a failed upload never blocks or reverses an approval. The row is stored as `FAILED` with a safe message, the metric `EvidencePackFailed` fires an alarm, and the UI says the audit copy could not be saved.
* **Document upload is FUTURE.** No upload endpoint exists. The planned path is upload to `uploads/<tenant>/…`, extraction, normalisation, then analysis by REACT.

## 10. Bedrock configuration

1. In the Bedrock console for your region, enable access to the model you want and copy its model ID or inference-profile ID.
2. Deploy with `LLM_PROVIDER=bedrock BEDROCK_MODEL_ID=<that id> scripts/deploy_aws.sh`. The task role gets `bedrock:InvokeModel` on that model only.
3. Locally (optional): `LLM_PROVIDER=bedrock LLM_MODEL=<id> AWS_PROFILE=<profile> uvicorn app.main:app`.
4. Live check (costs one short request): `REACT_AWS_LIVE=1 LLM_MODEL=<id> AWS_REGION=<region> pytest tests/aws_live -m aws_live`.

**What the model receives (data minimisation):**

* the finding as detected, with its titles, metrics and record IDs;
* the deterministic hypotheses and evidence items with claims, values and IDs;
* **summaries** of tool results: counts, key names, at most a capped list of record IDs, and summary statistics. It never receives raw record dumps.
* no customer contact details, no credentials, no connection strings.

The system prompts forbid computing money and forbid chain-of-thought in the output. Every structured output is validated against a schema and against the deterministic results before use.

## 11. Agent configuration and limits

| Guard | Setting | Effect |
|---|---|---|
| Iterations per run | `AGENT_MAX_ITERATIONS=4` | Tool-selection loop stops |
| Tool calls per run | `AGENT_MAX_TOOL_CALLS=12` | Run ends as `INSUFFICIENT_EVIDENCE` |
| LLM calls per run | `AGENT_MAX_LLM_CALLS=10` | Same |
| Wall clock per run | `AGENT_MAX_EXECUTION_SECONDS` (50 on AWS) | Same |
| Retries per LLM step | `LLM_MAX_RETRIES=1`; botocore retries off for Bedrock | No retry storms |
| LLM calls per hour, all tasks | `LLM_HOURLY_CALL_BUDGET=200` | Provider replaced by *unavailable* and the fallback runs (`LLMBudgetExhausted` metric) |
| Fallback | `AGENT_FALLBACK_TO_DETERMINISTIC` | `true`: rule-based result labelled `fallback_used`. `false`: HTTP 503 "AI investigation temporarily unavailable" |

## 12. Observability

* **Logs:** one JSON object per line on stdout, shipped to the CloudWatch log group `ApiLogs` with 30-day retention. Keys that look like secrets (`password`, `secret`, `api_key`, `authorization`, `session_token` and similar) are masked, and passwords inside connection URLs are replaced with `[REDACTED]` (`app/observability/logging.py`, tested).
* **Metrics** (namespace `REACT`, one dimension `Environment`, fixed list so no metric sprawl):

| Area | Metrics |
|---|---|
| Agents | `AgentRunCompleted`, `AgentRunFailed`, `AgentRunDurationMs`, `FallbackUsed`, `AIUnavailable` |
| LLM cost | `LLMCalls`, `LLMInputTokens`, `LLMOutputTokens`, `LLMBudgetExhausted` |
| Decisions | `DecisionCreated`, `ActionExecuted`, `ActionFailed`, `EvidencePackStored`, `EvidencePackFailed` |
| Failures | `ToolCallFailed`, `DatabaseUnavailable`, `UnhandledError` |

* **API metrics** come from the ALB: requests, target 5xx, latency p50 and p95, unhealthy hosts.
* **Dashboard** `REACT-<env>` shows all of the above plus ECS CPU and memory and RDS CPU and connections.
* **Alarms (7):** API 5xx, unhealthy targets, RDS CPU, RDS free storage, `AIUnavailable`, `DatabaseUnavailable`, `EvidencePackFailed`. E-mail delivery only when `alarmEmail` is set.
* **Agent trace:** the *visible audit trace* (agent, action, tool, arguments, result summary, evidence IDs, state, duration, error, concise summary) stays in PostgreSQL and is copied into every evidence pack. Private model reasoning is never requested, stored or logged.
* **Health:** `/health` is liveness. It always returns 200 and reports database status; the ALB and container health check use it. `/ready` returns 200 or 503 and checks the database, Alembic revision, seeded data, storage configuration and AI provider state. It makes no Bedrock or S3 call and returns no hosts, URLs, bucket names or ARNs.

## 13. Cost considerations

Approximate list prices for a staging stack left running 24/7 in us-east-1. Use the AWS Pricing Calculator for your region.

| Item | Rough monthly cost | Control |
|---|---|---|
| ALB | ~$17–20 | One ALB; destroy after the event |
| Fargate, 1 task (0.5 vCPU, 1 GiB) | ~$18 | `desiredCount` 1, `maxCount` 3 hard ceiling |
| RDS db.t4g.micro + 20 GiB gp3 | ~$14 | Storage autoscaling capped at 50 GiB |
| Public IPv4 addresses (ALB and task) | ~$11 | No NAT gateway, which would cost about $32 more |
| CloudFront, S3, Secrets Manager, CloudWatch | a few dollars | Caching for UI assets only; 30-day log retention; 17 fixed custom metrics |
| Bedrock | per token, only in `bedrock` mode | Per-run limits, hourly budget, `LLMInputTokens` / `LLMOutputTokens` on the dashboard |
| **Total without Bedrock** | **~$60–70** | `cdk destroy` when idle; Budgets alert with `alarmEmail` |

Other safeguards:

* no infinite retries anywhere;
* no scheduled jobs or background loops;
* readiness never calls a paid API;
* synthetic dataset size is fixed by `HISTORY_DAYS`;
* the CI workflow needs no AWS account.

## 14. Security considerations

* **Network:**
  * RDS sits in isolated subnets with no internet route.
  * The ALB admits only CloudFront's origin-facing address list.
  * Tasks admit only the ALB.
  * Nothing is open to `0.0.0.0/0`, and the infra tests check this.
* **Encryption:**
  * RDS storage and both buckets are encrypted at rest.
  * Browser to CloudFront uses HTTPS, with HTTP redirected.
  * The app connects to RDS with `sslmode=require`.
  * **Gap:** CloudFront to ALB uses HTTP, because the stack has no custom domain or ACM certificate. The traffic stays between CloudFront and an ALB that only CloudFront can reach, and all data is synthetic. **Production must add a domain, an ACM certificate on the ALB and CloudFront, and HTTPS to the origin.**
* **Secrets:**
  * The database secret lives in Secrets Manager and is injected at container start.
  * The repository, image, frontend bundle and `.env.example` contain no secrets.
  * `.dockerignore` excludes every `.env*` file.
  * CI checks the image has no `.env` and runs as UID 10001, not root.
* **Supply chain:**
  * The base image is pinned by digest and Python packages are pinned by hash.
  * CI runs `pip-audit` and `npm audit`; all three currently report 0 known vulnerabilities.
* **Authentication is not implemented (known risk).** Anyone with the CloudFront URL can view the synthetic data, approve simulated actions and, in staging, switch the AI-failure drill. This is acceptable only because every action is simulated and all data is synthetic. Cognito sign-in and per-reviewer identity are prerequisites for real data. `DEMO_CONTROLS_ENABLED=false` in production.
* **Browser:** CloudFront adds the managed security-headers policy (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy).

## 15. Failure handling

| Failure | What REACT does | Verified by |
|---|---|---|
| Bedrock unreachable, throttled or denied | Error sanitised (no ARNs or account IDs). One retry, then the deterministic fallback labelled `fallback_used`, or 503 "AI investigation temporarily unavailable" when fallback is off | `test_aws_stubbed.py`, `test_operations_api.py`, Vitest, Playwright `ai-failure.spec.ts` |
| Invalid LLM output | Schema and grounding validation reject it; retry with the error; then fallback or failure. Never used unvalidated | Phase 3 tests |
| Agent timeout, too many steps | Run ends as `INSUFFICIENT_EVIDENCE` with no recommendation | Phase 3 tests |
| Tool failure | Recorded in the trace and `agent_tool_calls`; `ToolCallFailed` metric | Phase 3 tests |
| Database unavailable | 503 "The REACT database is temporarily unavailable. No data was changed." with no driver text; `/health` reports `DOWN`; `/ready` returns 503 | `test_operations_api.py` |
| S3 unavailable | Approval completes; evidence pack row `FAILED`; alarm; UI notice | `test_operations_api.py`, Vitest |
| Unhandled error | 500 with a reference ID only; details stay in the logs; `UnhandledError` metric | `test_operations_api.py` |
| Bad deployment | ECS circuit breaker rolls back to the last healthy task definition | CDK assertion (not exercised live) |
| Migration not applied | `/ready` returns 503 (`BEHIND`); the deploy script's smoke test fails | `test_operations_api.py` |

**Safe AI-failure demo:** on *Data & System*, choose *AI down, no substitute*. Investigating a finding now shows "AI investigation temporarily unavailable" and no conclusion. *AI down, use rule-based analysis* returns the evidence-based result with a visible label. The drill switches the provider to one that never makes a network call, so it is safe on AWS too. `POST /demo/ai-outage {"mode": "off" | "fallback" | "fail"}`.

## 16. Rollback strategy

| Layer | How to roll back |
|---|---|
| API | Automatic: the ECS deployment circuit breaker restores the previous task definition. Manual: redeploy the previous commit with `scripts/deploy_aws.sh`. |
| UI | Re-sync the previous `frontend/dist` and invalidate `/index.html`. Old hashed assets remain in the bucket. |
| Infrastructure | CloudFormation rolls back a failed stack update automatically; `cdk diff` before every deploy. |
| Database schema | Migrations are additive (expand first, contract in a later release), so the previous image runs on the new schema. `alembic downgrade -1` exists for each of the Phase 6 and merchant revisions. |
| Data | RDS automated backups (7 days staging, 14 production) with point-in-time restore; a final snapshot on stack deletion. |
| Evidence packs | S3 versioning keeps overwritten or deleted versions for 90 days. |

## 17. Scaling from 1 business to 1,000

| Scale | What changes | Already in place |
|---|---|---|
| **1 merchant** (today) | Nothing | Stateless API, 1 task, db.t4g.micro |
| **10 merchants** | Add a `tenant_id` column to business tables with a row-level filter in services, sign-in (Cognito), and per-tenant S3 prefixes | Autoscaling to 3 tasks; the advisory-locked migrations; S3 key layout already has a per-decision path |
| **100–1,000 merchants** | Investigations move to SQS plus ECS workers (API returns a run ID and the UI polls); scheduled scans per tenant through EventBridge; RDS to a larger class or Aurora with a reader; RDS Proxy; per-tenant LLM budgets | The orchestrator already persists state per run, so moving it into a worker is a deployment change |
| **Large scale** | Isolate the largest tenants (schema or cluster per tenant), Step Functions for long approval waits, AgentCore if agents need isolation, WAF and per-tenant rate limits | Money stays in the deterministic engine, and evidence packs keep decisions auditable across any storage split |

## 18. Provenance

Every decision still traces to its investigation, then hypothesis, evidence items and source record IDs in PostgreSQL. Evidence packs in S3 carry the same IDs plus the seed and data window, so an archived decision can be matched to the exact synthetic dataset that produced it. Every simulated action in a pack records `business_records_modified=false`.

## 19. Tests

| Tier | Command | Needs |
|---|---|---|
| Unit (no database) | `pytest -m unit` | nothing |
| Integration | `pytest -m integration` | local PostgreSQL |
| AWS live (opt-in, small cost) | `REACT_AWS_LIVE=1 pytest tests/aws_live -m aws_live` | AWS credentials, `LLM_MODEL`, `ARTIFACTS_BUCKET` |
| Infrastructure | `cd infra && npm test && npx cdk synth` | Node |
| Frontend | `cd frontend && npm test && npm run e2e` | Node, Chromium, local PostgreSQL |

## 20. Known limitations

* Nothing is deployed, and Bedrock and S3 have not been called live. No claim in this repository should be read as a deployment.
* The Docker image has not been built locally; CI builds it.
* There is no authentication (see Security).
* CloudFront to ALB is HTTP until a custom domain and certificate are added.
* Investigations run synchronously and are capped at 50 seconds on AWS.
* The AI-failure drill and the LLM budget are global, not per tenant (there are no tenants yet).
* API tasks use public subnets with public IPs; there is no NAT gateway, to save cost.
