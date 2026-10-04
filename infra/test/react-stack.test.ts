/**
 * Offline checks of the synthesized CloudFormation (no AWS account needed). They pin the security, cost and
 * honesty properties the architecture document claims, so a later edit cannot silently break them.
 */
import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import * as ecs from "aws-cdk-lib/aws-ecs";
import { beforeAll, describe, expect, it } from "vitest";
import { ReactStack, type ReactStackProps } from "../lib/react-stack.js";

const image = ecs.ContainerImage.fromRegistry("public.ecr.aws/docker/library/python:3.12-slim");
const synth = (props: Partial<ReactStackProps> = {}) => {
  const app = new App();
  const stack = new ReactStack(app, `T${Math.random().toString(36).slice(2, 8)}`, { envName: "staging", containerImage: image, ...props });
  return Template.fromStack(stack);
};
const allPolicyStatements = (t: Template) =>
  Object.values(t.findResources("AWS::IAM::Policy")).flatMap((p: any) => p.Properties.PolicyDocument.Statement as any[]);
const asList = <T,>(v: T | T[]) => (Array.isArray(v) ? v : [v]);

describe("REACT stack (staging, deterministic provider)", () => {
  let t: Template;
  beforeAll(() => {
    t = synth();
  });

  it("uses exactly the services the architecture claims", () => {
    for (const type of ["AWS::EC2::VPC", "AWS::RDS::DBInstance", "AWS::SecretsManager::Secret", "AWS::ECS::Cluster", "AWS::ECS::Service", "AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::CloudFront::Distribution", "AWS::Logs::LogGroup", "AWS::CloudWatch::Dashboard"]) {
      t.resourceCountIs(type, 1);
    }
    t.resourceCountIs("AWS::S3::Bucket", 2);
    t.resourceCountIs("AWS::EC2::NatGateway", 0); // largest avoidable fixed cost
    for (const absent of ["AWS::ElastiCache::CacheCluster", "AWS::ElastiCache::ReplicationGroup", "AWS::SQS::Queue", "AWS::StepFunctions::StateMachine", "AWS::RDS::DBCluster", "AWS::EC2::Instance"]) {
      t.resourceCountIs(absent, 0); // proposed/future only; never deployed silently
    }
  });

  it("keeps the database private, encrypted and backed up, with a generated secret", () => {
    t.hasResourceProperties("AWS::RDS::DBInstance", { Engine: "postgres", PubliclyAccessible: false, StorageEncrypted: true, BackupRetentionPeriod: 7, MultiAZ: false, DBInstanceClass: "db.t4g.micro", MaxAllocatedStorage: 50 });
    t.hasResourceProperties("AWS::SecretsManager::Secret", { GenerateSecretString: Match.objectLike({ GenerateStringKey: "password" }) });
    // the only way into the database security group is from the API tasks' security group
    const dbIngress = Object.values(t.findResources("AWS::EC2::SecurityGroupIngress")).filter((r: any) => JSON.stringify(r.Properties.GroupId).includes("DatabaseSecurityGroup")) as any[];
    expect(dbIngress).toHaveLength(1);
    expect(JSON.stringify(dbIngress[0].Properties.SourceSecurityGroupId)).toContain("ApiServiceSecurityGroup");
  });

  it("injects DB credentials from Secrets Manager and never as plain environment", () => {
    const td = Object.values(t.findResources("AWS::ECS::TaskDefinition"))[0] as any;
    const c = td.Properties.ContainerDefinitions[0];
    const envNames = c.Environment.map((e: any) => e.Name);
    expect(c.Secrets.map((s: any) => s.Name).sort()).toEqual(["DB_PASSWORD", "DB_USER"]);
    expect(envNames).not.toContain("DB_PASSWORD");
    expect(envNames).not.toContain("DATABASE_URL");
    for (const e of c.Environment) expect(String(JSON.stringify(e.Value))).not.toMatch(/AKIA|aws_secret|password=/i);
    const env = Object.fromEntries(c.Environment.map((e: any) => [e.Name, e.Value]));
    expect(env).toMatchObject({ REACT_DOMAIN: "merchant", STORAGE_BACKEND: "s3", METRICS_ENABLED: "true", AUTO_CREATE_SCHEMA: "false", LLM_PROVIDER: "deterministic", LLM_MODEL: "", DEMO_CONTROLS_ENABLED: "true", AGENT_MAX_EXECUTION_SECONDS: "50" });
    expect(c.HealthCheck.Command.join(" ")).toContain("/health");
  });

  it("grants the application least privilege: no admin, no wildcard actions, S3 limited to the evidence-pack prefix", () => {
    const managed = JSON.stringify(t.findResources("AWS::IAM::Role"));
    expect(managed).not.toContain("AdministratorAccess");
    expect(managed).not.toContain("PowerUserAccess");
    for (const s of allPolicyStatements(t)) {
      for (const a of asList(s.Action)) expect(a === "*" || /:\*$/.test(a)).toBe(false);
    }
    const s3 = allPolicyStatements(t).find((s) => s.Sid === "EvidencePacksReadWrite");
    expect(asList(s3.Action).sort()).toEqual(["s3:GetObject", "s3:PutObject"]);
    expect(JSON.stringify(s3.Resource)).toContain("/evidence-packs/*");
    expect(JSON.stringify(allPolicyStatements(t))).not.toContain("bedrock:"); // deterministic mode: no Bedrock permission at all
  });

  it("blocks public access and enforces TLS on both buckets", () => {
    const buckets = Object.values(t.findResources("AWS::S3::Bucket")) as any[];
    for (const b of buckets) {
      expect(b.Properties.PublicAccessBlockConfiguration).toEqual({ BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true });
      expect(b.Properties.BucketEncryption).toBeDefined();
    }
    const policies = JSON.stringify(t.findResources("AWS::S3::BucketPolicy"));
    expect(policies).toContain("aws:SecureTransport");
  });

  it("routes /api/* to the ALB without caching, the rest to private S3 through Origin Access Control", () => {
    t.resourceCountIs("AWS::CloudFront::OriginAccessControl", 1);
    const dist = (Object.values(t.findResources("AWS::CloudFront::Distribution"))[0] as any).Properties.DistributionConfig;
    const apiBehavior = dist.CacheBehaviors.find((b: any) => b.PathPattern === "/api/*");
    expect(apiBehavior.CachePolicyId).toBe("4135ea2d-6df8-44a3-9df3-4b5a84be39ad"); // managed CachingDisabled
    expect(apiBehavior.ViewerProtocolPolicy).toBe("https-only");
    expect(dist.DefaultCacheBehavior.ViewerProtocolPolicy).toBe("redirect-to-https");
    expect(dist.DefaultCacheBehavior.FunctionAssociations).toHaveLength(1);
  });

  it("lets only CloudFront reach the load balancer, and nothing is open to the internet", () => {
    const inline = Object.values(t.findResources("AWS::EC2::SecurityGroup")).flatMap((r: any) => r.Properties.SecurityGroupIngress ?? []);
    const standalone = Object.values(t.findResources("AWS::EC2::SecurityGroupIngress")).map((r: any) => r.Properties);
    for (const rule of [...inline, ...standalone]) {
      expect(rule.CidrIp).not.toBe("0.0.0.0/0");
      expect(rule.CidrIpv6).not.toBe("::/0");
    }
    const albIngress = standalone.filter((r: any) => JSON.stringify(r.GroupId).includes("ApiLBSecurityGroup"));
    expect(albIngress).toHaveLength(1);
    expect(albIngress[0]).toMatchObject({ FromPort: 80, ToPort: 80 });
    expect(JSON.stringify(albIngress[0].SourcePrefixListId)).toContain("CloudFrontOriginPrefixList");
  });

  it("caps cost: bounded autoscaling, log retention, rollback on failed deploys", () => {
    t.hasResourceProperties("AWS::ApplicationAutoScaling::ScalableTarget", { MinCapacity: 1, MaxCapacity: 3 });
    t.hasResourceProperties("AWS::Logs::LogGroup", { RetentionInDays: 30 });
    t.hasResourceProperties("AWS::ECS::Service", { DesiredCount: 1, DeploymentConfiguration: Match.objectLike({ DeploymentCircuitBreaker: { Enable: true, Rollback: true } }) });
    t.resourceCountIs("AWS::Budgets::Budget", 0); // only with an alarm e-mail
  });

  it("alarms on API errors, unhealthy tasks, database pressure and outages, AI unavailability and evidence-pack failures", () => {
    t.resourceCountIs("AWS::CloudWatch::Alarm", 7);
    t.hasResourceProperties("AWS::CloudWatch::Alarm", { Namespace: "REACT", MetricName: "AIUnavailable", Dimensions: [{ Name: "Environment", Value: "staging" }] });
  });
});

describe("REACT stack variants", () => {
  it("bedrock mode grants InvokeModel on the configured model only", () => {
    const t = synth({ llmProvider: "bedrock", bedrockModelId: "global.example-model-v1" });
    const s = allPolicyStatements(t).find((x) => x.Sid === "BedrockInvokeConfiguredModelOnly");
    expect(asList(s.Action)).toEqual(["bedrock:InvokeModel"]);
    const res = JSON.stringify(s.Resource);
    expect(res).toContain("foundation-model/example-model-v1");
    expect(res).toContain("inference-profile/global.example-model-v1");
    expect(res).not.toContain("foundation-model/*");
    const td = Object.values(t.findResources("AWS::ECS::TaskDefinition"))[0] as any;
    const env = Object.fromEntries(td.Properties.ContainerDefinitions[0].Environment.map((e: any) => [e.Name, e.Value]));
    expect(env).toMatchObject({ LLM_PROVIDER: "bedrock", LLM_MODEL: "global.example-model-v1" });
  });

  it("refuses bedrock mode without a configured model ID", () => {
    expect(() => synth({ llmProvider: "bedrock" })).toThrow(/bedrockModelId/);
  });

  it("production hardens the database and disables demo controls", () => {
    const t = synth({ envName: "production", alarmEmail: "ops@example.com" });
    t.hasResourceProperties("AWS::RDS::DBInstance", { MultiAZ: true, DeletionProtection: true, BackupRetentionPeriod: 14 });
    const td = Object.values(t.findResources("AWS::ECS::TaskDefinition"))[0] as any;
    const env = Object.fromEntries(td.Properties.ContainerDefinitions[0].Environment.map((e: any) => [e.Name, e.Value]));
    expect(env.DEMO_CONTROLS_ENABLED).toBe("false");
    t.resourceCountIs("AWS::Budgets::Budget", 1);
    t.resourceCountIs("AWS::SNS::Topic", 1);
  });
});
