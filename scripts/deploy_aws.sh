#!/usr/bin/env bash
# Deploy REACT to AWS (staging by default). Requires: AWS credentials for the target account (SSO, profile or the
# GitHub OIDC role; never long-lived keys in this repo), Docker (CDK builds the API image), Node 22, the AWS CLI.
#
#   scripts/deploy_aws.sh                                   # staging, rule-based provider
#   LLM_PROVIDER=bedrock BEDROCK_MODEL_ID=<id> scripts/deploy_aws.sh
#   ENV_NAME=production ALARM_EMAIL=ops@example.com scripts/deploy_aws.sh
#
# Steps: cdk bootstrap (idempotent) -> cdk deploy (image build + push, stack update, ECS rolling deploy with
# circuit-breaker rollback) -> build UI -> sync to the site bucket -> CloudFront invalidation -> /api/ready smoke test.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_NAME="${ENV_NAME:-staging}"
STACK="React-${ENV_NAME}"
CTX=(-c "envName=${ENV_NAME}" -c "llmProvider=${LLM_PROVIDER:-deterministic}")
[ -n "${BEDROCK_MODEL_ID:-}" ] && CTX+=(-c "bedrockModelId=${BEDROCK_MODEL_ID}")
[ -n "${ALARM_EMAIL:-}" ] && CTX+=(-c "alarmEmail=${ALARM_EMAIL}")
[ -n "${MONTHLY_BUDGET_USD:-}" ] && CTX+=(-c "monthlyBudgetUsd=${MONTHLY_BUDGET_USD}")

aws sts get-caller-identity --query Account --output text >/dev/null  # fails fast without credentials

echo "==> infrastructure + API image (${STACK})"
( cd infra && npm ci && npx cdk bootstrap "${CTX[@]}" && npx cdk deploy "${STACK}" "${CTX[@]}" --require-approval never --outputs-file cdk-outputs.json )
out() { node -e "const o=require('./infra/cdk-outputs.json')['${STACK}']; process.stdout.write(o['$1'])"; }
SITE_BUCKET="$(out SiteBucketName)"; DIST_ID="$(out DistributionId)"; APP_URL="$(out AppUrl)"

echo "==> UI"
( cd frontend && npm ci && npm run build )
# hashed assets: cache for a year; index.html: always revalidate
aws s3 sync frontend/dist "s3://${SITE_BUCKET}" --delete --exclude index.html --cache-control "public,max-age=31536000,immutable"
aws s3 cp frontend/dist/index.html "s3://${SITE_BUCKET}/index.html" --cache-control "no-cache"
aws cloudfront create-invalidation --distribution-id "${DIST_ID}" --paths "/index.html" "/" >/dev/null

echo "==> smoke test ${APP_URL}/api/ready"
for i in $(seq 1 30); do
  if curl -fsS "${APP_URL}/api/ready" >/dev/null; then echo "READY: ${APP_URL}"; exit 0; fi
  sleep 10
done
echo "API did not become ready. Check the ECS service events and the CloudWatch log group." >&2
exit 1
