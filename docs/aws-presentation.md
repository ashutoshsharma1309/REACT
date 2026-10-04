# REACT Merchant on AWS: slide material

For the AWS Innovation Challenge deck. Every row below is backed by code in this repository.

**Say it accurately.** "REACT's AWS deployment is defined in AWS CDK and tested offline" is true today. "REACT runs on AWS" becomes true only after `scripts/deploy_aws.sh` has run and `/api/ready` answers. Tag each claim on the slide as *implemented*, *demo-verified* or *planned*.

## Implemented (application code + infrastructure code, tests passing; not yet deployed)

| AWS service | Why we use it | REACT component | Benefit |
|---|---|---|---|
| **Amazon ECS on AWS Fargate** | Run the FastAPI API and agent orchestrator as containers | API, REACT core, agents | Same image as local; health checks; automatic rollback of failed deploys; autoscaling with a hard ceiling |
| **Application Load Balancer** | Health-checked entry point for API tasks, reachable only from CloudFront | API layer | Zero-downtime rolling deploys; unhealthy tasks replaced |
| **Amazon CloudFront** | One HTTPS address for the UI and `/api/*` | Frontend delivery | TLS, India edge locations, no CORS, no backend URL in the browser bundle |
| **Amazon S3** | Private UI hosting and **evidence packs** (audit copy of every approve or reject, SHA-256 verified) | Frontend, audit storage | Durable, encrypted, versioned audit trail that outlives the database row |
| **Amazon RDS for PostgreSQL** | Relational business data, investigations, decisions, agent trace | REACT database (SQLAlchemy + Alembic unchanged) | Managed backups, patching, encryption; private subnets |
| **AWS Secrets Manager** | Generated database credentials, injected at container start | Database access | No password in git, the image or the browser |
| **Amazon ECR** | Container image registry | Build and deploy | Versioned, reproducible images |
| **Amazon CloudWatch** | JSON logs, 17 application metrics (EMF), dashboard, 7 alarms | Observability | Agent runs, LLM calls and tokens, fallbacks, AI outages, evidence-pack and database failures at a glance |
| **AWS IAM** | Task role limited to the evidence-pack prefix (and one Bedrock model when enabled) | Security | Least privilege; no admin rights; no API keys |
| **Amazon VPC** | Isolated subnets for the database; CloudFront-only load balancer | Network security | Database never reachable from the internet |
| **Amazon Bedrock** *(optional mode; adapter tested offline, not yet called live)* | Agent reasoning: structured outputs, tool selection, explanations | `LLMProvider` → `BedrockProvider` (Converse API) | Managed model access with IAM. **The model never calculates money.** It falls back to rule-based analysis, or says "AI investigation temporarily unavailable". |

Optional with an alert e-mail: **Amazon SNS** (alarm e-mails) and **AWS Budgets** (cost alerts at 50%, 80% and forecast 100%).

## Planned, not built (say "planned")

| AWS service | Would add | When it becomes worth it |
|---|---|---|
| AWS Step Functions | Durable multi-day approval waits, cross-restart retries | Asynchronous investigations or long approval SLAs |
| Amazon Bedrock AgentCore | Managed agent runtime and tool gateway | Agent isolation or multi-tenant agent hosting |
| Amazon SQS + ECS workers | Queued investigations | Many merchants scanning at once, or runs longer than 50 s |
| Amazon Cognito | Sign-in and real reviewer identity | Before any real merchant data |
| Amazon Textract + S3 upload | Document ingestion | The *Upload business data* feature (shown as "coming soon") |
| Amazon ElastiCache, Amazon Aurora, AWS WAF | Cache, bigger database, edge rate limiting | Hundreds of tenants or a public launch |

## One-line architecture

Browser → CloudFront → (S3 UI | ALB → ECS Fargate FastAPI → RDS PostgreSQL + S3 evidence packs + Bedrock, optional) → CloudWatch

## Talking points

* **Safety first.**
  * Money comes only from the deterministic impact engine.
  * The LLM picks tools and writes explanations, and every output is validated against stored evidence.
  * Every action needs a human approval and is simulated.
* **Fails safely.** If the AI is down, REACT either uses its rule-based analysis and says so, or answers "AI investigation temporarily unavailable". It never produces a guessed answer. This is shown live with the *AI failure drill*.
* **Auditable.** Each approval writes an evidence pack to S3 with a SHA-256. A download re-verifies the hash, and a tampered file is refused.
* **Cost-aware.**
  * No NAT gateway, and at most 3 tasks.
  * Per-run agent limits plus an hourly LLM call budget shared by all tasks.
  * 30-day log retention and budget alerts.
  * About $60–70 a month if left running; destroy it after the event.
