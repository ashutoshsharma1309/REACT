/**
 * REACT on AWS: one stack per environment (staging by default).
 *
 *   Browser ──HTTPS──▶ CloudFront ──┬── /*      ──▶ S3 (private, Origin Access Control)   React SPA
 *                                   └── /api/*  ──▶ ALB (only reachable from CloudFront) ──▶ ECS Fargate: FastAPI
 *                                                                                           ├──▶ RDS PostgreSQL (isolated subnets)
 *                                                                                           ├──▶ S3 artifacts bucket (evidence packs)
 *                                                                                           ├──▶ Amazon Bedrock (only when llmProvider=bedrock)
 *                                                                                           └──▶ CloudWatch Logs + EMF metrics
 *   Secrets Manager: RDS-generated database credentials, injected into the container by ECS (never in code, image or git).
 *
 * Every service here is used by running code. Nothing is added for the diagram. Cost guard rails are explicit props.
 * No account ID, credential or model ID is hard-coded: they come from the CDK environment and context.
 */
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { Aws, CfnOutput, Duration, RemovalPolicy, Stack, type StackProps, Tags } from "aws-cdk-lib";
import * as budgets from "aws-cdk-lib/aws-budgets";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cwActions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Platform } from "aws-cdk-lib/aws-ecr-assets";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecsPatterns from "aws-cdk-lib/aws-ecs-patterns";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as rds from "aws-cdk-lib/aws-rds";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subs from "aws-cdk-lib/aws-sns-subscriptions";
import * as cr from "aws-cdk-lib/custom-resources";
import type { Construct } from "constructs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
export const ARTIFACT_PREFIX = "evidence-packs/";

export interface ReactStackProps extends StackProps {
  /** staging | production. Production turns on deletion protection, Multi-AZ and disables demo controls. */
  readonly envName: "staging" | "production";
  /** deterministic (default, no Bedrock permission granted) | bedrock (requires bedrockModelId) */
  readonly llmProvider?: "deterministic" | "bedrock";
  /** Bedrock model ID or inference-profile ID enabled in the target account/region. Never defaulted. */
  readonly bedrockModelId?: string;
  /** Normal number of API tasks, and the hard ceiling autoscaling may reach (cost guard). */
  readonly desiredCount?: number;
  readonly maxCount?: number;
  /** Real LLM calls per rolling hour across all tasks (application-level cost guard). */
  readonly llmHourlyCallBudget?: number;
  /** CloudWatch Logs retention (cost guard: logs never grow forever). */
  readonly logRetention?: logs.RetentionDays;
  /** Optional: e-mail for alarms and the monthly AWS Budget. Without it, alarms still show in the console. */
  readonly alarmEmail?: string;
  readonly monthlyBudgetUsd?: number;
  /** Tests pass a registry image; normally the image is built from the repository Dockerfile. */
  readonly containerImage?: ecs.ContainerImage;
}

export class ReactStack extends Stack {
  constructor(scope: Construct, id: string, props: ReactStackProps) {
    super(scope, id, props);
    const prod = props.envName === "production";
    const llmProvider = props.llmProvider ?? "deterministic";
    if (llmProvider === "bedrock" && !props.bedrockModelId) {
      throw new Error("llmProvider=bedrock requires bedrockModelId (pass -c bedrockModelId=<id enabled in your account>)");
    }
    const desiredCount = props.desiredCount ?? 1;
    const maxCount = Math.max(desiredCount, props.maxCount ?? 3);
    Tags.of(this).add("project", "react");
    Tags.of(this).add("environment", props.envName);

    // ---- Network ------------------------------------------------------------------------------------------------
    // Two AZs. Public subnets hold the ALB and the API tasks (public IP for egress to ECR / Bedrock / Secrets Manager,
    // no inbound except from the ALB security group). Isolated subnets hold RDS (no route to the internet at all).
    // No NAT gateway: it would be the largest fixed cost of a staging environment. See docs/aws-architecture.md.
    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: "public", subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: "data", subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
      gatewayEndpoints: { S3: { service: ec2.GatewayVpcEndpointAwsService.S3 } }, // free; S3 traffic stays in AWS
    });

    // ---- Database: RDS PostgreSQL + Secrets Manager -------------------------------------------------------------
    const database = new rds.DatabaseInstance(this, "Database", {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_16 }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, prod ? ec2.InstanceSize.SMALL : ec2.InstanceSize.MICRO),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      credentials: rds.Credentials.fromGeneratedSecret("react_app"), // password generated into Secrets Manager
      databaseName: "react",
      allocatedStorage: 20,
      maxAllocatedStorage: 50, // storage autoscaling ceiling (cost guard)
      storageType: rds.StorageType.GP3,
      storageEncrypted: true,
      multiAz: prod,
      publiclyAccessible: false,
      backupRetention: Duration.days(prod ? 14 : 7),
      deletionProtection: prod,
      removalPolicy: prod ? RemovalPolicy.RETAIN : RemovalPolicy.SNAPSHOT,
      autoMinorVersionUpgrade: true,
    });

    // ---- S3: artifacts (evidence packs) and the static UI ------------------------------------------------------
    const artifacts = new s3.Bucket(this, "ArtifactsBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true, // an overwritten or deleted evidence pack can be recovered
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      lifecycleRules: [{ noncurrentVersionExpiration: Duration.days(90), abortIncompleteMultipartUploadAfter: Duration.days(1) }],
      removalPolicy: RemovalPolicy.RETAIN, // audit records outlive the stack
    });
    const site = new s3.Bucket(this, "SiteBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // ---- Logs ---------------------------------------------------------------------------------------------------
    const logGroup = new logs.LogGroup(this, "ApiLogs", {
      retention: props.logRetention ?? logs.RetentionDays.ONE_MONTH,
      removalPolicy: prod ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
    });

    // ---- API: ECS Fargate behind an Application Load Balancer ---------------------------------------------------
    const cluster = new ecs.Cluster(this, "Cluster", { vpc });
    const taskDefinition = new ecs.FargateTaskDefinition(this, "ApiTask", {
      cpu: 512,
      memoryLimitMiB: 1024,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.X86_64, operatingSystemFamily: ecs.OperatingSystemFamily.LINUX },
    });
    const secret = database.secret!;
    taskDefinition.addContainer("api", {
      image: props.containerImage ?? ecs.ContainerImage.fromAsset(REPO_ROOT, { platform: Platform.LINUX_AMD64 }),
      portMappings: [{ containerPort: 8000 }],
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: "api", logGroup }),
      environment: {
        APP_ENV: props.envName,
        LOG_LEVEL: "INFO",
        AWS_REGION: Aws.REGION,
        DB_HOST: database.dbInstanceEndpointAddress,
        DB_PORT: database.dbInstanceEndpointPort,
        DB_NAME: "react",
        DB_SSLMODE: "require",
        AUTO_CREATE_SCHEMA: "false", // Alembic is the source of truth on AWS
        RUN_MIGRATIONS: "true", // serialized across tasks with a PostgreSQL advisory lock
        SEED_ON_STARTUP: "true", // synthetic demo dataset, seeded once (advisory lock)
        REACT_DOMAIN: "merchant", // REACT Merchant demo business (Astra Retail); synthetic, labelled SYNTHETIC
        WEB_CONCURRENCY: "2",
        STORAGE_BACKEND: "s3",
        ARTIFACTS_BUCKET: artifacts.bucketName,
        ARTIFACTS_PREFIX: ARTIFACT_PREFIX,
        METRICS_ENABLED: "true",
        METRICS_NAMESPACE: "REACT",
        API_PATH_PREFIX: "/api",
        CORS_ORIGINS: "", // the UI and API share the CloudFront origin: no cross-origin access needed
        LLM_PROVIDER: llmProvider,
        LLM_MODEL: props.bedrockModelId ?? "",
        LLM_TIMEOUT_SECONDS: "20",
        LLM_MAX_RETRIES: "1",
        AGENT_MAX_EXECUTION_SECONDS: "50", // below CloudFront's 60 s origin timeout
        AGENT_FALLBACK_TO_DETERMINISTIC: "true",
        LLM_HOURLY_CALL_BUDGET: String(props.llmHourlyCallBudget ?? 200),
        DEMO_CONTROLS_ENABLED: prod ? "false" : "true",
      },
      secrets: {
        DB_USER: ecs.Secret.fromSecretsManager(secret, "username"),
        DB_PASSWORD: ecs.Secret.fromSecretsManager(secret, "password"),
      },
      healthCheck: {
        command: ["CMD", "python", "-c", "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4); sys.exit(0)"],
        interval: Duration.seconds(30),
        timeout: Duration.seconds(5),
        retries: 3,
        startPeriod: Duration.seconds(120),
      },
    });

    const api = new ecsPatterns.ApplicationLoadBalancedFargateService(this, "Api", {
      cluster,
      taskDefinition,
      desiredCount,
      publicLoadBalancer: true,
      openListener: false, // ingress is added below for CloudFront only
      assignPublicIp: true,
      taskSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      circuitBreaker: { rollback: true }, // a failed deployment rolls back automatically
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
      healthCheckGracePeriod: Duration.seconds(180), // first boot migrates and seeds the synthetic dataset
    });
    api.targetGroup.configureHealthCheck({ path: "/health", healthyHttpCodes: "200", interval: Duration.seconds(30), timeout: Duration.seconds(5), healthyThresholdCount: 2, unhealthyThresholdCount: 3 });
    api.targetGroup.setAttribute("deregistration_delay.timeout_seconds", "30");
    api.loadBalancer.setAttribute("idle_timeout.timeout_seconds", "65");
    api.loadBalancer.setAttribute("routing.http.drop_invalid_header_fields.enabled", "true");
    database.connections.allowDefaultPortFrom(api.service, "REACT API tasks only");

    // Only CloudFront may reach the ALB: AWS-managed prefix list of CloudFront origin-facing addresses (read at deploy time).
    const cfPrefixList = new cr.AwsCustomResource(this, "CloudFrontOriginPrefixList", {
      onUpdate: {
        service: "EC2",
        action: "describeManagedPrefixLists",
        parameters: { Filters: [{ Name: "prefix-list-name", Values: ["com.amazonaws.global.cloudfront.origin-facing"] }] },
        physicalResourceId: cr.PhysicalResourceId.of("cloudfront-origin-facing-prefix-list"),
        outputPaths: ["PrefixLists.0.PrefixListId"],
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([new iam.PolicyStatement({ actions: ["ec2:DescribeManagedPrefixLists"], resources: ["*"] })]), // describe-only; this action has no resource-level scoping
      installLatestAwsSdk: false,
    });
    api.loadBalancer.connections.allowFrom(ec2.Peer.prefixList(cfPrefixList.getResponseField("PrefixLists.0.PrefixListId")), ec2.Port.tcp(80), "CloudFront origin-facing addresses only");

    const scaling = api.service.autoScaleTaskCount({ minCapacity: desiredCount, maxCapacity: maxCount }); // hard ceiling = cost guard
    scaling.scaleOnCpuUtilization("CpuScaling", { targetUtilizationPercent: 60, scaleInCooldown: Duration.seconds(180), scaleOutCooldown: Duration.seconds(60) });

    // ---- IAM: least privilege for the application (task role) -------------------------------------------------
    // The execution role (image pull, log writes, reading the one DB secret) is generated by CDK with scoped grants.
    const taskRole = taskDefinition.taskRole;
    taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      sid: "EvidencePacksReadWrite",
      actions: ["s3:PutObject", "s3:GetObject"],
      resources: [artifacts.arnForObjects(`${ARTIFACT_PREFIX}*`)],
    }));
    if (llmProvider === "bedrock" && props.bedrockModelId) {
      const modelId = props.bedrockModelId;
      const baseModel = modelId.replace(/^(us|eu|apac|global|jp|au|ca|us-gov)\./, "");
      taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
        sid: "BedrockInvokeConfiguredModelOnly",
        actions: ["bedrock:InvokeModel"], // Converse uses the InvokeModel permission; no streaming, no other models
        resources: [
          `arn:${Aws.PARTITION}:bedrock:*::foundation-model/${baseModel}`,
          `arn:${Aws.PARTITION}:bedrock:${Aws.REGION}:${Aws.ACCOUNT_ID}:inference-profile/${modelId}`,
        ],
      }));
    }

    // ---- CloudFront: one HTTPS origin for the UI and the API ----------------------------------------------------
    const spaRewrite = new cloudfront.Function(this, "SpaRewrite", {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      comment: "Serve index.html for client-side routes (paths without a file extension). API paths never reach this behaviour.",
      code: cloudfront.FunctionCode.fromInline("function handler(event){var r=event.request;if(r.uri.indexOf('.')===-1){r.uri='/index.html';}return r;}"),
    });
    const distribution = new cloudfront.Distribution(this, "Cdn", {
      comment: `REACT ${props.envName}`,
      defaultRootObject: "index.html",
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200, // includes India edge locations
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(site),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
        functionAssociations: [{ function: spaRewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
        compress: true,
      },
      additionalBehaviors: {
        "/api/*": {
          origin: new origins.LoadBalancerV2Origin(api.loadBalancer, { protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY, readTimeout: Duration.seconds(60), keepaliveTimeout: Duration.seconds(60) }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED, // business data is never cached at the edge
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
        },
      },
    });

    // ---- Observability: dashboard + alarms (+ optional notifications and budget) ------------------------------
    const env = { Environment: props.envName };
    const app = (name: string, statistic = "Sum") => new cloudwatch.Metric({ namespace: "REACT", metricName: name, dimensionsMap: env, statistic, period: Duration.minutes(5) });
    const alarms: cloudwatch.Alarm[] = [
      new cloudwatch.Alarm(this, "Api5xxAlarm", { alarmDescription: "API returned 5xx responses", metric: api.loadBalancer.metrics.httpCodeTarget(elbv2.HttpCodeTarget.TARGET_5XX_COUNT, { period: Duration.minutes(5), statistic: "Sum" }), threshold: 5, evaluationPeriods: 1, treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING }),
      new cloudwatch.Alarm(this, "UnhealthyTargetsAlarm", { alarmDescription: "An API task is failing its /health check", metric: api.targetGroup.metrics.unhealthyHostCount({ period: Duration.minutes(1), statistic: "Maximum" }), threshold: 1, evaluationPeriods: 3, comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD, treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING }),
      new cloudwatch.Alarm(this, "DbCpuAlarm", { alarmDescription: "RDS CPU above 80%", metric: database.metricCPUUtilization({ period: Duration.minutes(5) }), threshold: 80, evaluationPeriods: 3 }),
      new cloudwatch.Alarm(this, "DbStorageAlarm", { alarmDescription: "RDS free storage below 2 GiB", metric: database.metricFreeStorageSpace({ period: Duration.minutes(5) }), threshold: 2 * 1024 ** 3, evaluationPeriods: 1, comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD }),
      new cloudwatch.Alarm(this, "AiUnavailableAlarm", { alarmDescription: "Investigations failed because the AI provider was unavailable and fallback was not allowed", metric: app("AIUnavailable"), threshold: 1, evaluationPeriods: 1, comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD, treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING }),
      new cloudwatch.Alarm(this, "DatabaseUnavailableAlarm", { alarmDescription: "API requests failed because the database was unreachable", metric: app("DatabaseUnavailable"), threshold: 1, evaluationPeriods: 1, comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD, treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING }),
      new cloudwatch.Alarm(this, "EvidencePackFailedAlarm", { alarmDescription: "Evidence packs could not be written to S3", metric: app("EvidencePackFailed"), threshold: 1, evaluationPeriods: 1, comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD, treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING }),
    ];
    if (props.alarmEmail) {
      const topic = new sns.Topic(this, "AlarmTopic", { displayName: `REACT ${props.envName} alarms` });
      topic.addSubscription(new subs.EmailSubscription(props.alarmEmail));
      alarms.forEach((a) => a.addAlarmAction(new cwActions.SnsAction(topic)));
      new budgets.CfnBudget(this, "MonthlyBudget", {
        budget: { budgetName: `react-${props.envName}-monthly`, budgetType: "COST", timeUnit: "MONTHLY", budgetLimit: { amount: props.monthlyBudgetUsd ?? 50, unit: "USD" } },
        notificationsWithSubscribers: [50, 80, 100].map((pct) => ({ notification: { notificationType: pct === 100 ? "FORECASTED" : "ACTUAL", comparisonOperator: "GREATER_THAN", threshold: pct, thresholdType: "PERCENTAGE" }, subscribers: [{ subscriptionType: "EMAIL", address: props.alarmEmail! }] })),
      });
    }
    new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: `REACT-${props.envName}`,
      widgets: [
        [
          new cloudwatch.GraphWidget({ title: "API requests and errors", left: [api.loadBalancer.metrics.requestCount({ period: Duration.minutes(5) })], right: [api.loadBalancer.metrics.httpCodeTarget(elbv2.HttpCodeTarget.TARGET_5XX_COUNT, { period: Duration.minutes(5) })] }),
          new cloudwatch.GraphWidget({ title: "API latency p50 / p95", left: [api.loadBalancer.metrics.targetResponseTime({ statistic: "p50" }), api.loadBalancer.metrics.targetResponseTime({ statistic: "p95" })] }),
          new cloudwatch.GraphWidget({ title: "ECS CPU / memory %", left: [api.service.metricCpuUtilization(), api.service.metricMemoryUtilization()] }),
        ],
        [
          new cloudwatch.GraphWidget({ title: "Agent runs", left: [app("AgentRunCompleted"), app("AgentRunFailed"), app("FallbackUsed"), app("AIUnavailable")] }),
          new cloudwatch.GraphWidget({ title: "LLM usage (cost driver)", left: [app("LLMCalls"), app("LLMBudgetExhausted")], right: [app("LLMInputTokens"), app("LLMOutputTokens")] }),
          new cloudwatch.GraphWidget({ title: "Decisions, actions, evidence packs", left: [app("DecisionCreated"), app("ActionExecuted"), app("ActionFailed"), app("EvidencePackStored"), app("EvidencePackFailed")] }),
        ],
        [
          new cloudwatch.GraphWidget({ title: "Agent run duration (ms)", left: [app("AgentRunDurationMs", "p50"), app("AgentRunDurationMs", "p95")] }),
          new cloudwatch.GraphWidget({ title: "Failures: tools, database, application", left: [app("ToolCallFailed"), app("DatabaseUnavailable"), app("UnhandledError")] }),
          new cloudwatch.GraphWidget({ title: "RDS CPU % / connections", left: [database.metricCPUUtilization()], right: [database.metricDatabaseConnections()] }),
          new cloudwatch.AlarmStatusWidget({ title: "Alarms", alarms }),
        ],
      ],
    });

    // ---- Outputs (names only; no secret values) -----------------------------------------------------------------
    new CfnOutput(this, "AppUrl", { value: `https://${distribution.distributionDomainName}` });
    new CfnOutput(this, "DistributionId", { value: distribution.distributionId });
    new CfnOutput(this, "SiteBucketName", { value: site.bucketName });
    new CfnOutput(this, "ArtifactsBucketName", { value: artifacts.bucketName });
    new CfnOutput(this, "ClusterName", { value: cluster.clusterName });
    new CfnOutput(this, "ServiceName", { value: api.service.serviceName });
    new CfnOutput(this, "LogGroupName", { value: logGroup.logGroupName });
    new CfnOutput(this, "DatabaseSecretArn", { value: secret.secretArn, description: "ARN only; the value stays in Secrets Manager" });
  }
}
