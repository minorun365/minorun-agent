import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cdk from 'aws-cdk-lib';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import type { Construct } from 'constructs';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const knowledgeDir = path.join(currentDir, '../../knowledge');

// 知識の置き場：S3（kb/ は知識ベースの元、sessions/ はセッションカタログの控え）と Managed KB。
// kb/ の中身は knowledge/dist/kb（`python3 knowledge/tools/build_kb.py` で作る）をそのまま写す。
// 取り込みの開始と検索の確かめは infra/scripts/knowledge.py で行う。
export class KnowledgeStack extends cdk.Stack {
  readonly bucket: s3.Bucket;
  readonly knowledgeBase: bedrock.CfnKnowledgeBase;
  readonly toolsGateway: agentcore.CfnGateway;

  constructor(scope: Construct, id: string, props: cdk.StackProps) {
    super(scope, id, props);

    this.bucket = new s3.Bucket(this, 'Data', {
      bucketName: `minorun-agent-knowledge-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    new s3deploy.BucketDeployment(this, 'DeployKb', {
      sources: [s3deploy.Source.asset(path.join(knowledgeDir, 'dist/kb'))],
      destinationBucket: this.bucket,
      destinationKeyPrefix: 'kb/',
      memoryLimit: 512,
    });

    const role = new iam.Role(this, 'KbRole', {
      roleName: 'minorun-agent-kb',
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: { 'aws:SourceArn': `arn:${this.partition}:bedrock:${this.region}:${this.account}:knowledge-base/*` },
        },
      }),
    });
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [this.bucket.bucketArn],
      conditions: { StringEquals: { 'aws:ResourceAccount': this.account } },
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'],
      resources: [this.bucket.arnForObjects('kb/*')],
      conditions: { StringEquals: { 'aws:ResourceAccount': this.account } },
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:ListFoundationModels', 'bedrock:ListCustomModels'],
      resources: ['*'],
    }));

    // 埋め込み・分割・保管はすべて Bedrock 任せ（MANAGED）
    this.knowledgeBase = new bedrock.CfnKnowledgeBase(this, 'Kb', {
      name: 'minorun-agent-kb',
      description: 'みのるんエージェントの知識（re:Invent の参加の実務。付箋 year で 2026 年と過去を分ける）',
      roleArn: role.roleArn,
      knowledgeBaseConfiguration: {
        type: 'MANAGED',
        managedKnowledgeBaseConfiguration: { embeddingModelType: 'MANAGED' },
      },
    });
    this.knowledgeBase.node.addDependency(role);

    const dataSource = new bedrock.CfnDataSource(this, 'KbSource', {
      knowledgeBaseId: this.knowledgeBase.attrKnowledgeBaseId,
      name: 'minorun-agent-kb-s3',
      dataSourceConfiguration: {
        type: 'MANAGED_KNOWLEDGE_BASE_CONNECTOR',
        managedKnowledgeBaseConnectorConfiguration: {
          connectorParameters: {
            type: 'S3',
            version: '1',
            connectionConfiguration: { bucketName: this.bucket.bucketName, bucketOwnerAccountId: this.account },
            filterConfiguration: { inclusionPrefixes: ['kb/'] },
          },
        },
      },
    });

    // セッションカタログの控えを取り直す（11月は1時間ごと・会期中は15分ごと。判定は Lambda の中）
    const refreshLogs = new logs.LogGroup(this, 'SessionsRefreshLogs', {
      logGroupName: '/aws/lambda/minorun-agent-sessions-refresh',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const refresh = new lambda.Function(this, 'SessionsRefresh', {
      functionName: 'minorun-agent-sessions-refresh',
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'sessions_lambda.handler',
      code: lambda.Code.fromAsset(path.join(knowledgeDir, 'tools'), {
        exclude: ['*', '!fetch_sessions.py', '!sessions_lambda.py'],
      }),
      timeout: cdk.Duration.minutes(5),
      memorySize: 1024,
      logGroup: refreshLogs,
      environment: { BUCKET: this.bucket.bucketName },
    });
    this.bucket.grantReadWrite(refresh, 'sessions/*');
    new events.Rule(this, 'SessionsRefreshSchedule', {
      schedule: events.Schedule.rate(cdk.Duration.minutes(15)),
      targets: [new targets.LambdaFunction(refresh, { retryAttempts: 0 })],
    });

    // ツールの Gateway（MCP・IAM 認証）が引き受けるロール。知識ベースの検索と Web 検索だけを許す。
    const toolsRole = new iam.Role(this, 'ToolsGatewayRole', {
      roleName: 'minorun-agent-tools-gateway',
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: { 'aws:SourceArn': `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:gateway/*` },
        },
      }),
    });
    toolsRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:GetKnowledgeBase', 'bedrock:Retrieve'],
      resources: [this.knowledgeBase.attrKnowledgeBaseArn],
    }));
    toolsRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeWebSearch', 'bedrock-agentcore:GetGateway'],
      resources: ['*'],
    }));

    // ツールの Gateway。ターゲットは3つで、知識ベースは1つのまま、年の絞り込みを管理者側で固定した
    // Retrieve を2つ並べる（エージェントが渡せるのは質問文だけ。年の書き間違いで過去の情報が混ざらない）。
    //   kb-2026  year = 2026（公式＋2026年の参加者情報）
    //   kb-past  year < 2026（過去の参考）
    //   web      AgentCore Web Search
    // 2026-10-25 に tools_gateway.py で作ったものを `cdkd import` で取り込んだ（URL は変わっていない）。
    this.toolsGateway = new agentcore.CfnGateway(this, 'ToolsGateway', {
      name: 'minorun-agent-tools',
      description: 'みのるんエージェントの道具（知識ベースの2026年・過去と Web 検索）',
      roleArn: toolsRole.roleArn,
      protocolType: 'MCP',
      authorizerType: 'AWS_IAM',
    });
    const kbTarget = (name: string, description: string, filter: object) => new agentcore.CfnGatewayTarget(this, `ToolsTarget-${name}`, {
      gatewayIdentifier: this.toolsGateway.attrGatewayIdentifier,
      name,
      credentialProviderConfigurations: [{ credentialProviderType: 'GATEWAY_IAM_ROLE' }],
      targetConfiguration: { mcp: { connector: {
        source: { connectorId: 'bedrock-knowledge-bases' },
        configurations: [{
          name: 'Retrieve',
          description,
          parameterValues: {
            knowledgeBaseId: this.knowledgeBase.attrKnowledgeBaseId,
            retrievalConfiguration: { managedSearchConfiguration: { numberOfResults: 8, filter } },
          },
          parameterOverrides: [{
            path: '$.retrievalQuery.text',
            description: '探したい内容（日本語の自然な文。例：バッジの受け取り場所）',
            visible: true,
          }],
        }],
      } } },
    });
    kbTarget('kb-2026', '2026年の re:Invent の情報（公式サイトと2026年の参加者の情報）を探す',
      { equals: { key: 'year', value: 2026 } });
    kbTarget('kb-past', '2025年以前の re:Invent の参加記・資料（過去の参考）を探す',
      { lessThan: { key: 'year', value: 2026 } });
    new agentcore.CfnGatewayTarget(this, 'ToolsTarget-web', {
      gatewayIdentifier: this.toolsGateway.attrGatewayIdentifier,
      name: 'web',
      credentialProviderConfigurations: [{ credentialProviderType: 'GATEWAY_IAM_ROLE' }],
      targetConfiguration: { mcp: { connector: {
        source: { connectorId: 'web-search' },
        configurations: [{ name: 'WebSearch', parameterValues: {} }],
      } } },
    });

    new cdk.CfnOutput(this, 'ToolsGatewayUrl', { value: this.toolsGateway.attrGatewayUrl });
    new cdk.CfnOutput(this, 'ToolsGatewayRoleArn', { value: toolsRole.roleArn });
    new cdk.CfnOutput(this, 'BucketName', { value: this.bucket.bucketName });
    new cdk.CfnOutput(this, 'KnowledgeBaseId', { value: this.knowledgeBase.attrKnowledgeBaseId });
    new cdk.CfnOutput(this, 'DataSourceId', { value: dataSource.attrDataSourceId });
  }
}
