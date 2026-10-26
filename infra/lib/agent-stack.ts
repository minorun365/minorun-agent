import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cdk from 'aws-cdk-lib';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import type { Construct } from 'constructs';

const currentDir = path.dirname(fileURLToPath(import.meta.url));

export interface AgentStackProps extends cdk.StackProps {
  // ツールの Gateway（MinorunKnowledge が持つ）
  readonly toolsGateway: { gatewayArn: string; mcpUrl: string };
  readonly knowledgeBucketName: string;
}

export class AgentStack extends cdk.Stack {
  readonly runtime: agentcore.CfnRuntime;
  readonly legacyRuntime: agentcore.CfnRuntime;
  readonly runtimeV2: agentcore.CfnRuntime;
  readonly conversationLog: dynamodb.Table;

  constructor(scope: Construct, id: string, props: AgentStackProps) {
    super(scope, id, props);

    const image = new ecrAssets.DockerImageAsset(this, 'RuntimeImage', {
      directory: path.join(currentDir, '../../agent'),
      platform: ecrAssets.Platform.LINUX_ARM64,
      exclude: ['.venv', '**/__pycache__', '**/*.pyc'],
      ignoreMode: cdk.IgnoreMode.GLOB,
    });

    const role = new iam.Role(this, 'RuntimeRole', {
      roleName: 'minorun-agent-runtime',
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
    });
    role.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    image.repository.grantPull(role);
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:${this.partition}:bedrock:*::foundation-model/*`,
        `arn:${this.partition}:bedrock:*:${this.account}:inference-profile/*`,
      ],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:DescribeLogGroups'], resources: ['*'] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [
        `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:*`,
      ],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['xray:PutTelemetryRecords', 'xray:PutTraceSegments', 'cloudwatch:PutMetricData'],
      resources: ['*'],
    }));

    // 会話中の文脈（やり取りと話題の棚の控え）。7日で消える（2026-10-25 決定）
    const memory = new agentcore.CfnMemory(this, 'Memory', {
      name: 'minorun_agent_memory',
      description: 'みのるんエージェントの会話の文脈（7日）と、ログインした人の長期記憶',
      eventExpiryDuration: 7,
      // Google でログインした人の会話（conversational のイベント）だけから、好みと事実を抜き出して残す（2026-10-25 みのるん指示）
      memoryStrategies: [
        { userPreferenceMemoryStrategy: { name: 'preferences', namespaces: ['/users/{actorId}/preferences'] } },
        { semanticMemoryStrategy: { name: 'facts', namespaces: ['/users/{actorId}/facts'] } },
      ],
    });
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:CreateEvent', 'bedrock-agentcore:ListEvents', 'bedrock-agentcore:GetEvent',
        'bedrock-agentcore:RetrieveMemoryRecords'],
      resources: [memory.attrMemoryArn],
    }));

    // 後から分析するための1問1行。期限なし（2026-10-25 決定）。pk = 会話ID、sk = 時刻
    const log = new dynamodb.Table(this, 'ConversationLog', {
      tableName: 'minorun-agent-conversations',
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      // 1行ごとの「最初の文字までの時間」などを MinorunWatch がメトリクスにする
      stream: dynamodb.StreamViewType.NEW_IMAGE,
    });
    this.conversationLog = log;
    log.grantWriteData(role);

    // 道具：ツールの Gateway、セッションカタログの控え（読む・照合結果を書き戻す）
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeGateway'],
      resources: [props.toolsGateway.gatewayArn],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject'],
      resources: [`arn:${this.partition}:s3:::${props.knowledgeBucketName}/sessions/*`],
    }));
    // 現地からの速報（knowledge/tools/announce.py が書き、道具 get_announcements が読む）
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'],
      resources: [`arn:${this.partition}:s3:::${props.knowledgeBucketName}/announcements/*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [`arn:${this.partition}:s3:::${props.knowledgeBucketName}`],
      conditions: { StringLike: { 's3:prefix': ['sessions/*'] } },
    }));
    // 鮮度の問いは Brave を直接呼ぶ。キーは AgentCore Identity の保管庫に API キーの資格情報 brave として先に作っておく
    const vault = `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`;
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:GetResourceApiKey'],
      resources: [vault, `${vault}/apikeycredentialprovider/brave`,
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [`arn:${this.partition}:secretsmanager:${this.region}:${this.account}:secret:bedrock-agentcore-identity!default/apikey/brave-*`],
    }));

    // 受け口は IAM（authorizerConfiguration なし）。呼べるのは入口の Gateway のロールだけにする。
    const runtimeProps: agentcore.CfnRuntimeProps = {
      agentRuntimeName: 'minorun_agent',
      description: 'みのるんエージェント（re:Invent 2026 の Q&A）',
      agentRuntimeArtifact: { containerConfiguration: { containerUri: image.imageUri } },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      roleArn: role.roleArn,
      environmentVariables: {
        AGENT_OBSERVABILITY_ENABLED: 'true',
        OTEL_PYTHON_DISTRO: 'aws_distro',
        OTEL_PYTHON_CONFIGURATOR: 'aws_configurator',
        OTEL_EXPORTER_OTLP_PROTOCOL: 'http/protobuf',
        // 質問と回答の本文をトレースに残す（既定では抜かれ、Evaluations も読めない）
        AWS_GENAI_CONTENT_EXTRACTION_OPT_OUT: 'true',
        MODEL_MAIN: 'global.anthropic.claude-sonnet-5-5',
        MODEL_AUX: 'global.anthropic.claude-haiku-5-5',
        MAIN_EFFORT: 'low',
        TOOLS_GATEWAY_URL: props.toolsGateway.mcpUrl,
        KNOWLEDGE_BUCKET: props.knowledgeBucketName,
        MEMORY_ID: memory.attrMemoryId,
        LOG_TABLE: log.tableName,
        ASSET_BASE: '/assets',
      },
    };
    // V1 は切り戻し用に現行イメージを固定し、V2 と保存先・実行権限を共有する。
    const legacyImage = this.node.tryGetContext('runtimeV1Image') ?? process.env.RUNTIME_V1_IMAGE ?? image.imageUri;
    const active = this.node.tryGetContext('runtimeActive');
    if (!legacyImage || !['V1', 'V2'].includes(active)) throw new Error('runtimeV1Image と runtimeActive (V1/V2) が必要です');
    this.legacyRuntime = new agentcore.CfnRuntime(this, 'Runtime', {
      ...runtimeProps,
      agentRuntimeArtifact: { containerConfiguration: { containerUri: legacyImage } },
    });
    this.runtimeV2 = new agentcore.CfnRuntime(this, 'RuntimeV2', {
      ...runtimeProps, agentRuntimeName: 'minorun_agent_v2', platformVersion: 'V2',
    });
    this.runtime = active === 'V2' ? this.runtimeV2 : this.legacyRuntime;

    // トレースのログは180日（2026-10-25 決定）。ロググループは Runtime を作った瞬間に
    // AgentCore が自分で作る（既定30日）ため、CDK からは作れない。できた後に保持期間だけ設定する。
    for (const [id, runtime] of [['RuntimeLogRetention', this.legacyRuntime], ['RuntimeV2LogRetention', this.runtimeV2]] as const) {
      const logGroupName = `/aws/bedrock-agentcore/runtimes/${runtime.attrAgentRuntimeId}-DEFAULT`;
      const retention = {
        service: 'CloudWatchLogs',
        action: 'putRetentionPolicy',
        parameters: { logGroupName, retentionInDays: logs.RetentionDays.SIX_MONTHS },
        physicalResourceId: cr.PhysicalResourceId.of(logGroupName),
      };
      new cr.AwsCustomResource(this, id, {
        onCreate: retention,
        onUpdate: retention,
        policy: cr.AwsCustomResourcePolicy.fromStatements([new iam.PolicyStatement({
          actions: ['logs:PutRetentionPolicy'],
          resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
        })]),
        installLatestAwsSdk: false,
      });

    }
    new cdk.CfnOutput(this, 'RuntimeV2Arn', { value: this.runtimeV2.attrAgentRuntimeArn });
    new cdk.CfnOutput(this, 'RuntimeV1Arn', { value: this.legacyRuntime.attrAgentRuntimeArn });
    new cdk.CfnOutput(this, 'RuntimeArn', { value: this.runtime.attrAgentRuntimeArn });
  }
}
