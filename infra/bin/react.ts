#!/usr/bin/env node
/**
 * CDK app entry. Account and region come from the deploying credentials (CDK_DEFAULT_ACCOUNT / CDK_DEFAULT_REGION);
 * nothing account-specific is committed. Options arrive as context, for example:
 *   npx cdk deploy -c envName=staging -c llmProvider=bedrock -c bedrockModelId=<id> -c alarmEmail=ops@example.com
 */
import { App } from "aws-cdk-lib";
import type * as logs from "aws-cdk-lib/aws-logs";
import { ReactStack } from "../lib/react-stack.js";

const app = new App();
const ctx = (key: string): string | undefined => {
  const v = app.node.tryGetContext(key);
  return v === undefined || v === null || v === "" ? undefined : String(v);
};
const envName = (ctx("envName") ?? "staging") as "staging" | "production";
if (!["staging", "production"].includes(envName)) throw new Error(`envName must be staging or production (got ${envName})`);

new ReactStack(app, `React-${envName}`, {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION },
  description: `REACT (Real-time Event Analysis & Crisis Triage) ${envName}: CloudFront + S3 UI, ECS Fargate API, RDS PostgreSQL, S3 evidence packs`,
  envName,
  llmProvider: (ctx("llmProvider") ?? "deterministic") as "deterministic" | "bedrock",
  bedrockModelId: ctx("bedrockModelId"),
  desiredCount: ctx("desiredCount") ? Number(ctx("desiredCount")) : undefined,
  maxCount: ctx("maxCount") ? Number(ctx("maxCount")) : undefined,
  llmHourlyCallBudget: ctx("llmHourlyCallBudget") ? Number(ctx("llmHourlyCallBudget")) : undefined,
  logRetention: ctx("logRetentionDays") ? (Number(ctx("logRetentionDays")) as logs.RetentionDays) : undefined,
  alarmEmail: ctx("alarmEmail"),
  monthlyBudgetUsd: ctx("monthlyBudgetUsd") ? Number(ctx("monthlyBudgetUsd")) : undefined,
});
