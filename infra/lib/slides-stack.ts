import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import type { Construct } from 'constructs';

const currentDir = path.dirname(fileURLToPath(import.meta.url));

// URL の記事からスライドを作る機能（2026-11-15 みのるん決定。1人1日2本・全体で1日50本。11/21 からログインなしは1日1本）。
// エージェントのツール make_slides が回数の表で数えてからスライド工房（AgentCore Runtime）をジョブごとのセッションで起こし、
// できあがったビューア（s/<ジョブ>/）は CloudFront の /s/* から誰でも開ける（URL を知る人だけ・検索には出さない）。
export class SlidesStack extends cdk.Stack {
  readonly bucket: s3.Bucket;
  readonly quota: dynamodb.Table;
  readonly worker: agentcore.CfnRuntime;
  readonly workerLogGroup: string;

  constructor(scope: Construct, id: string, props: cdk.StackProps & { siteUrl: string }) {
    super(scope, id, props);

    // 配るのは CloudFront 経由だけ（バケットは非公開のまま）。作ったスライドは期限なしで残す
    this.bucket = new s3.Bucket(this, 'Decks', {
      bucketName: `minorun-agent-slides-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // CloudFront（OAC）から読ませる。配信は MinorunWeb の /s/*。配信の ARN を条件にすると2つのスタックが循環するので、アカウントで絞る
    this.bucket.addToResourcePolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject'],
      principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
      resources: [this.bucket.arnForObjects('s/*')],
      conditions: { StringEquals: { 'AWS:SourceAccount': this.account } },
    }));

    // pk = "user#<利用者のハッシュ>#<日本時間の日付>" と "all#<日付>" の件数。expires で3日後に消える
    this.quota = new dynamodb.Table(this, 'Quota', {
      tableName: 'minorun-agent-slides',
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Lambda のころ（2026-11-15）の工房のログ。監視ページが過去の分を読むので、Lambda を外しても残す
    new logs.LogGroup(this, 'WorkerLogs', {
      logGroupName: '/aws/lambda/minorun-agent-slides',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // スライド工房は AgentCore Runtime（2026-11-16 に Lambda から移した。回答のエージェントと同じ実行基盤にそろえる）。
    // make_slides がジョブごとに別のセッションで呼ぶので、1本ずつ別の入れ物で焼く
    const image = new ecrAssets.DockerImageAsset(this, 'WorkerImage', {
      directory: path.join(currentDir, '../../slides'),
      platform: ecrAssets.Platform.LINUX_ARM64,
      exclude: ['**/__pycache__'],
    });
    // 名前を minorun-agent- で始める：Claude API の ID 連携のルールが、この前方一致のロールだけを通す
    const workerRole = new iam.Role(this, 'WorkerRole', {
      roleName: 'minorun-agent-slides',
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
    });
    workerRole.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    image.repository.grantPull(workerRole);
    workerRole.addToPolicy(new iam.PolicyStatement({ actions: ['logs:DescribeLogGroups'], resources: ['*'] }));
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
      resources: [
        `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:*`,
      ],
    }));
    // Claude API の通行証と交換する身元の証明書を STS から受け取る
    workerRole.addToPolicy(new iam.PolicyStatement({ actions: ['sts:GetWebIdentityToken'], resources: ['*'] }));
    this.bucket.grantReadWrite(workerRole, 's/*');
    workerRole.addToPolicy(new iam.PolicyStatement({ actions: ['dynamodb:UpdateItem'], resources: [this.quota.tableArn] }));
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:${this.partition}:bedrock:*::foundation-model/*`,
        `arn:${this.partition}:bedrock:*:${this.account}:inference-profile/*`,
      ],
    }));
    // 普通の取得で読めなかったページを AgentCore Browser（AWS が用意する標準のブラウザ）で開く
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:StartBrowserSession', 'bedrock-agentcore:StopBrowserSession',
        'bedrock-agentcore:GetBrowserSession', 'bedrock-agentcore:ConnectBrowserAutomationStream'],
      resources: [`arn:${this.partition}:bedrock-agentcore:${this.region}:aws:browser/aws.browser.v1`],
    }));

    this.worker = new agentcore.CfnRuntime(this, 'Worker', {
      agentRuntimeName: 'minorun_agent_slides',
      description: 'URL の記事やテーマから minorun-glass のスライドを作ってビューアを公開する',
      agentRuntimeArtifact: { containerConfiguration: { containerUri: image.imageUri } },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      roleArn: workerRole.roleArn,
      // 焼き終えたら60秒で畳む（待っている間のメモリ代を払わない）。1本が20分を超えたら打ち切る（Lambda のときは10分）
      lifecycleConfiguration: { idleRuntimeSessionTimeout: 60, maxLifetime: 1200 },
      environmentVariables: {
        SLIDES_BUCKET: this.bucket.bucketName,
        SITE_URL: props.siteUrl,
        // 作れなかった1本を回数から戻すときに使う
        SLIDES_TABLE: this.quota.tableName,
        // 2026-11-15 みのるん決定（賢い方の Opus）。Bedrock の利用規約の同意は同日に済ませた
        SLIDES_MODEL: 'global.anthropic.claude-opus-5-5',
        SLIDES_EFFORT: 'medium',
        // 回答の Sonnet と同じく Claude API（Max プランの API クレジット）を先に使う。off で Bedrock だけに戻す（2026-11-15 みのるん指示）
        ANTHROPIC_PRIMARY: 'on',
        SLIDES_API_MODEL: 'claude-opus-5-5',
        // Claude Console の Workload identity で作った ID 連携（回答と同じルール）。秘密の値ではない
        CLAUDE_WIF_RULE_ID: process.env.CLAUDE_WIF_RULE_ID ?? '',
        CLAUDE_WIF_ORGANIZATION_ID: process.env.CLAUDE_WIF_ORGANIZATION_ID ?? '',
        CLAUDE_WIF_SERVICE_ACCOUNT_ID: process.env.CLAUDE_WIF_SERVICE_ACCOUNT_ID ?? '',
      },
    });
    this.worker.node.addDependency(workerRole);
    // 工房のログ（stdout）は AgentCore が自分で作るこのロググループに出る。監視ページの「スライド」タブが読む
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

    new cdk.CfnOutput(this, 'BucketName', { value: this.bucket.bucketName });
    new cdk.CfnOutput(this, 'WorkerArn', { value: this.worker.attrAgentRuntimeArn });
  }
}
