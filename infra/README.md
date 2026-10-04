# REACT infrastructure (AWS CDK, TypeScript)

The only infrastructure-as-code in this repository. One stack per environment: `React-staging` (default) or `React-production`. Architecture, IAM, costs and rollback are covered in [docs/aws-architecture.md](../docs/aws-architecture.md).

```bash
npm ci
npm run typecheck
npm test                                   # assertion tests on the synthesized template (no AWS account needed)
npx cdk synth                              # writes cdk.out/ (no AWS account needed)
npx cdk diff  -c envName=staging           # needs AWS credentials
../scripts/deploy_aws.sh                   # full deploy: stack + image + UI + smoke test
npx cdk destroy React-staging              # teardown (S3 buckets are retained on purpose)
```

| Context key | Default | Meaning |
|---|---|---|
| `envName` | `staging` | `staging` or `production` (production: Multi-AZ, deletion protection, 14-day backups, no demo controls) |
| `llmProvider` | `deterministic` | `bedrock` also grants `bedrock:InvokeModel` on one model |
| `bedrockModelId` | none | Required with `llmProvider=bedrock`; no default is built in |
| `desiredCount` / `maxCount` | 1 / 3 | Normal tasks and the autoscaling ceiling |
| `llmHourlyCallBudget` | 200 | LLM calls per hour across all tasks |
| `logRetentionDays` | 30 | CloudWatch Logs retention |
| `alarmEmail` / `monthlyBudgetUsd` | none / 50 | Adds SNS alarm e-mails and an AWS Budget |

The account and region come from your credentials (`CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION`). Nothing account-specific is committed.
