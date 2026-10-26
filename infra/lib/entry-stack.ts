import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cdk from 'aws-cdk-lib';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import type { Construct } from 'constructs';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
// ログイン（Google）の Cognito は infra/scripts/auth.py が作り、ID を infra/.auth.json に書く（無ければログインなしで動く）
const authFile = path.join(currentDir, '../.auth.json');
const auth = fs.existsSync(authFile) ? JSON.parse(fs.readFileSync(authFile, 'utf-8')) : undefined;

export interface EntryStackProps extends cdk.StackProps {
  readonly runtime: agentcore.CfnRuntime;
  readonly legacyRuntime: agentcore.CfnRuntime;
  readonly runtimeV2: agentcore.CfnRuntime;
  // 1問1行の会話の表。回答の評価（良い・良くない）を確認役がここへ書き足す
  readonly conversationLog: dynamodb.ITable;
}

// 入口：回数を数える表、確認役（インターセプター）、Gateway とそのロール。
// Gateway は 2026-10-25 に gateway.py で作ったものを `cdkd import` で取り込んだ（URL は変わっていない）。
export class EntryStack extends cdk.Stack {
  readonly usageTable: dynamodb.Table;
  readonly interceptor: lambda.Function;
  readonly gatewayRole: iam.Role;
  readonly gateway: agentcore.CfnGateway;
  // CloudFront の /api が向かう先（<Gateway の ID>.gateway.bedrock-agentcore.<リージョン>.amazonaws.com）
  readonly gatewayDomain: string;

  constructor(scope: Construct, id: string, props: EntryStackProps) {
    super(scope, id, props);

    // pk = "day#<日本時間の日付>#<匿名ID>" で1日の件数、pk = "switch" で停止スイッチ。expires で翌日に消える。
    this.usageTable = new dynamodb.Table(this, 'UsageTable', {
      tableName: 'minorun-agent-usage',
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const transportErrors = new dynamodb.Table(this, 'TransportErrors', {
      tableName: 'minorun-agent-transport-errors',
      partitionKey: { name: 'day', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const logGroup = new logs.LogGroup(this, 'InterceptorLogs', {
      logGroupName: '/aws/lambda/minorun-agent-interceptor',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.interceptor = new lambda.Function(this, 'Interceptor', {
      functionName: 'minorun-agent-interceptor',
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'interceptor.handler',
      code: lambda.Code.fromAsset(path.join(currentDir, '../lambda/interceptor'), { exclude: ['**/__pycache__', '**/*.pyc'] }),
      timeout: cdk.Duration.seconds(8),  // 評価は、裏で書かれる会話の行を最大2.5秒待って探す
      memorySize: 256,
      logGroup,
      environment: {
        TABLE_NAME: this.usageTable.tableName,
        ERROR_TABLE: transportErrors.tableName,
        DAILY_LIMIT: '30',
        MAX_CHARS: '1000',
        // ログインした人の上限（2026-10-25 みのるん指示「少し緩く」）
        USER_DAILY_LIMIT: '100',
        USER_PER_MINUTE: '10',
        CONVERSATIONS_TABLE: props.conversationLog.tableName,
        ...(auth ? { USER_POOL_ID: auth.userPoolId, USER_CLIENT_ID: auth.clientId } : {}),
      },
    });
    this.usageTable.grantReadWriteData(this.interceptor);
    transportErrors.grant(this.interceptor, 'dynamodb:UpdateItem');
    this.interceptor.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:Query', 'dynamodb:UpdateItem'],
      resources: [props.conversationLog.tableArn],
    }));

    this.gatewayRole = new iam.Role(this, 'GatewayRole', {
      roleName: 'minorun-agent-entry-gateway',
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
    });
    this.gatewayRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime'],
      resources: [props.legacyRuntime, props.runtimeV2].flatMap(r => [r.attrAgentRuntimeArn, `${r.attrAgentRuntimeArn}/*`]),
    }));
    this.interceptor.grantInvoke(this.gatewayRole);

    // Runtime は入口の Gateway のロールからの呼び出しだけを受け付ける（画面を通らない直接の呼び出しを断る）
    for (const [id, runtime] of [['RuntimeOnlyViaGateway', props.legacyRuntime], ['RuntimeV2OnlyViaGateway', props.runtimeV2]] as const) {
      new agentcore.CfnResourcePolicy(this, id, {
        resourceArn: runtime.attrAgentRuntimeArn,
        policy: cdk.Stack.of(this).toJsonString({
          Version: '2012-10-17',
          Statement: [{
            Sid: 'OnlyEntryGateway',
            Effect: 'Deny',
            Principal: '*',
            Action: 'bedrock-agentcore:InvokeAgentRuntime',
            Resource: runtime.attrAgentRuntimeArn,
            Condition: { ArnNotEquals: { 'aws:PrincipalArn': this.gatewayRole.roleArn } },
          }],
        }),
      });

    }

    // Runtime を HTTP で中継する Gateway。認証なし（画面にログインが無い）で、確認役が手前で数える
    this.gateway = new agentcore.CfnGateway(this, 'Gateway', {
      name: 'minorun-agent-entry',
      description: 'みのるんエージェントの入口（Runtime を HTTP で中継）',
      roleArn: this.gatewayRole.roleArn,
      authorizerType: 'NONE',
      interceptorConfigurations: [{
        interceptor: { lambda: { arn: this.interceptor.functionArn } },
        interceptionPoints: ['REQUEST'],
        inputConfiguration: { passRequestHeaders: true },
      }],
    });
    this.gateway.node.addDependency(this.gatewayRole);
    this.gatewayDomain = `${this.gateway.attrGatewayIdentifier}.gateway.bedrock-agentcore.${this.region}.amazonaws.com`;
    // ターゲット名は CloudFront Function の書き換え先（/minorun/invocations）と揃える
    const target = new agentcore.CfnGatewayTarget(this, 'GatewayTarget', {
      gatewayIdentifier: this.gateway.attrGatewayIdentifier,
      name: 'minorun',
      credentialProviderConfigurations: [{ credentialProviderType: 'GATEWAY_IAM_ROLE' }],
      targetConfiguration: { http: { agentcoreRuntime: { arn: props.runtime.attrAgentRuntimeArn } } },
    });
    // 認証なしの Gateway ではヘッダーごとの回数制限を作れない（2026-10-25 実測）。匿名IDごとの1分5件は
    // 確認役が数え、ここではターゲット全体の上限だけを持つ（費用の安全網）
    const rateLimit = new agentcore.CfnGatewayRateLimit(this, 'GatewayRateLimit', {
      gatewayIdentifier: this.gateway.attrGatewayIdentifier,
      description: '全体で1分60件',
      dimensionKeys: ['targetName'],
      entries: [{ dimensions: { targetName: 'minorun' }, requests: [{ rate: 60, period: 'minute' }] }],
    });
    rateLimit.addDependency(target);

    new cdk.CfnOutput(this, 'GatewayDomain', { value: this.gatewayDomain });
    new cdk.CfnOutput(this, 'InterceptorArn', { value: this.interceptor.functionArn });
    new cdk.CfnOutput(this, 'GatewayRoleArn', { value: this.gatewayRole.roleArn });
    new cdk.CfnOutput(this, 'UsageTableName', { value: this.usageTable.tableName });
  }
}
