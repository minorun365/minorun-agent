import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cdk from 'aws-cdk-lib';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as s3 from 'aws-cdk-lib/aws-s3';
import type { Construct } from 'constructs';

const currentDir = path.dirname(fileURLToPath(import.meta.url));

// 資料（PDF・PPTX）から、ずんだもんが3分で解説する動画を作る機能（2026-11-26 みのるん決定：ログインした人だけ1日1本・全体で1日20本、絵は出さず声だけ）。
// 画面が資料を一時置き場へ直接送り、確認役が回数を数えて起こし役の Lambda を非同期で呼ぶ。起こし役が動画作成機能（AgentCore Runtime）を
// ジョブごとのセッションで起こす。できた動画（v/<ジョブ>/）は CloudFront の /v/* から開ける（URL を知る人だけ・検索には出さない）
export class VideoStack extends cdk.Stack {
  readonly bucket: s3.Bucket;
  readonly quota: dynamodb.Table;
  readonly worker: agentcore.CfnRuntime;
  readonly launcher: lambda.Function;
  readonly workerLogGroup: string;

  constructor(scope: Construct, id: string, props: cdk.StackProps & { uploadBucketName: string }) {
    super(scope, id, props);

    this.bucket = new s3.Bucket(this, 'Videos', {
      bucketName: `minorun-agent-videos-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    // 配信は MinorunWeb の /v/*（OAC）。配信の ARN を条件にすると循環するので、アカウントで絞る
    this.bucket.addToResourcePolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'],
      principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
      resources: [this.bucket.arnForObjects('v/*')],
      conditions: { StringEquals: { 'AWS:SourceAccount': this.account } },
    }));

    // pk = "user#<利用者>#<日付>"・"all#<日付>" の件数（expires で3日後に消える）と、"list#<利用者>" の作った動画の一覧
    this.quota = new dynamodb.Table(this, 'Quota', {
      tableName: 'minorun-agent-videos',
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const uploads = s3.Bucket.fromBucketName(this, 'Uploads', props.uploadBucketName);

    // 動画作成機能。VOICEVOX ENGINE・LibreOffice・Chromium を同じ入れ物に入れるので大きく、platform V2 で動かす
    const image = new ecrAssets.DockerImageAsset(this, 'WorkerImage', {
      directory: path.join(currentDir, '../../video'),
      platform: ecrAssets.Platform.LINUX_ARM64,
      exclude: ['**/__pycache__'],
    });
    // 名前を minorun-agent- で始める：Claude API の ID 連携のルールが、この前方一致のロールだけを通す
    const role = new iam.Role(this, 'WorkerRole', {
      roleName: 'minorun-agent-video',
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
    });
    role.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    image.repository.grantPull(role);
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:DescribeLogGroups'], resources: ['*'] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
      resources: [
        `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:*`,
      ],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['sts:GetWebIdentityToken'], resources: ['*'] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:${this.partition}:bedrock:*::foundation-model/anthropic.claude-opus-5-5*`,
        `arn:${this.partition}:bedrock:*:${this.account}:inference-profile/global.anthropic.claude-opus-5-5*`,
      ],
    }));
    this.bucket.grantReadWrite(role, 'v/*');
    // 資料は読んだらすぐ消す（残りも置き場の期限の1日で消える）
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:DeleteObject'], resources: [uploads.arnForObjects('doc/*')],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['dynamodb:UpdateItem'], resources: [this.quota.tableArn] }));

    this.worker = new agentcore.CfnRuntime(this, 'Worker', {
      agentRuntimeName: 'minorun_agent_video',
      description: 'PDF・PPTX の資料から、ずんだもんが3分で解説する動画を作る',
      agentRuntimeArtifact: { containerConfiguration: { containerUri: image.imageUri } },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      platformVersion: 'V2',
      roleArn: role.roleArn,
      // 作り終えたら60秒で畳む。1本が30分を超えたら打ち切る（ふだんは5分前後）
      lifecycleConfiguration: { idleRuntimeSessionTimeout: 60, maxLifetime: 1800 },
      environmentVariables: {
        VIDEO_BUCKET: this.bucket.bucketName,
        UPLOAD_BUCKET: props.uploadBucketName,
        VIDEO_TABLE: this.quota.tableName,
        VIDEO_MODEL: 'global.anthropic.claude-opus-5-5',
        VIDEO_API_MODEL: 'claude-opus-5-5',
        VIDEO_EFFORT: 'medium',
        ANTHROPIC_PRIMARY: 'on',
        // Claude Console の Workload identity で作った ID 連携。未設定なら Bedrock だけで動く
        CLAUDE_WIF_RULE_ID: process.env.CLAUDE_WIF_RULE_ID ?? '',
        CLAUDE_WIF_ORGANIZATION_ID: process.env.CLAUDE_WIF_ORGANIZATION_ID ?? '',
        CLAUDE_WIF_SERVICE_ACCOUNT_ID: process.env.CLAUDE_WIF_SERVICE_ACCOUNT_ID ?? '',
      },
    });
    this.worker.node.addDependency(role);
    this.workerLogGroup = `/aws/bedrock-agentcore/runtimes/${this.worker.attrAgentRuntimeId}-DEFAULT`;
    const retention = {
      service: 'CloudWatchLogs',
      action: 'putRetentionPolicy',
      parameters: { logGroupName: this.workerLogGroup, retentionInDays: logs.RetentionDays.SIX_MONTHS },
      physicalResourceId: cr.PhysicalResourceId.of(this.workerLogGroup),
    };
    new cr.AwsCustomResource(this, 'WorkerLogRetention', {
      onCreate: retention,
      onUpdate: retention,
      policy: cr.AwsCustomResourcePolicy.fromStatements([new iam.PolicyStatement({
        actions: ['logs:PutRetentionPolicy', 'logs:CreateLogGroup'],
        resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
      })]),
      installLatestAwsSdk: false,
    });

    // 起こし役。確認役（8秒で返す）の代わりに、入れ物の立ち上がりを待って動画作成機能へジョブを渡す
    const launcherLogs = new logs.LogGroup(this, 'LauncherLogs', {
      logGroupName: '/aws/lambda/minorun-agent-video-launch',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.launcher = new lambda.Function(this, 'Launcher', {
      functionName: 'minorun-agent-video-launch',
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'launch.handler',
      code: lambda.Code.fromAsset(path.join(currentDir, '../lambda/video-launch'), { exclude: ['**/__pycache__', '**/*.pyc'] }),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      logGroup: launcherLogs,
      // 失敗しても呼び直さない（2本作らない）。失敗の後始末は起こし役の中で行う
      retryAttempts: 0,
      environment: {
        VIDEO_RUNTIME: this.worker.attrAgentRuntimeArn,
        VIDEO_BUCKET: this.bucket.bucketName,
        VIDEO_TABLE: this.quota.tableName,
      },
    });
    this.launcher.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime'],
      resources: [this.worker.attrAgentRuntimeArn, `${this.worker.attrAgentRuntimeArn}/*`],
    }));
    this.bucket.grantPut(this.launcher, 'v/*');
    this.launcher.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:UpdateItem'], resources: [this.quota.tableArn] }));

    new cdk.CfnOutput(this, 'BucketName', { value: this.bucket.bucketName });
    new cdk.CfnOutput(this, 'WorkerArn', { value: this.worker.attrAgentRuntimeArn });
    new cdk.CfnOutput(this, 'LauncherName', { value: this.launcher.functionName });
  }
}
