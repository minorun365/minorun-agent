import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import type { Construct } from 'constructs';

const currentDir = path.dirname(fileURLToPath(import.meta.url));

// URL の記事からスライドを作る機能（2026-11-15 みのるん決定。ログインした人だけ・1人1日2本・全体で1日50本）。
// エージェントのツール make_slides が回数の表で数えてからスライド工房を非同期で起動し、
// できあがったビューア（s/<ジョブ>/）は CloudFront の /s/* から誰でも開ける（URL を知る人だけ・検索には出さない）。
export class SlidesStack extends cdk.Stack {
  readonly bucket: s3.Bucket;
  readonly quota: dynamodb.Table;
  readonly worker: lambda.DockerImageFunction;

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

    const logGroup = new logs.LogGroup(this, 'WorkerLogs', {
      logGroupName: '/aws/lambda/minorun-agent-slides',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.worker = new lambda.DockerImageFunction(this, 'Worker', {
      functionName: 'minorun-agent-slides',
      description: 'URL の記事から minorun-glass のスライドを作ってビューアを公開する',
      code: lambda.DockerImageCode.fromImageAsset(path.join(currentDir, '../../slides'), {
        platform: cdk.aws_ecr_assets.Platform.LINUX_ARM64,
        exclude: ['**/__pycache__'],
      }),
      architecture: lambda.Architecture.ARM_64,
      memorySize: 3008,
      ephemeralStorageSize: cdk.Size.mebibytes(2048),
      timeout: cdk.Duration.minutes(10),
      // 全体の上限（1日50本）とは別に、同時に焼くのは3本まで
      reservedConcurrentExecutions: 3,
      retryAttempts: 0,
      logGroup,
      environment: {
        SLIDES_BUCKET: this.bucket.bucketName,
        SITE_URL: props.siteUrl,
        // 2026-11-15 みのるん決定（賢い方の Opus）。Bedrock の利用規約の同意は同日に済ませた
        SLIDES_MODEL: 'global.anthropic.claude-opus-5-5',
        SLIDES_EFFORT: 'medium',
      },
    });
    this.bucket.grantReadWrite(this.worker, 's/*');
    this.worker.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:${this.partition}:bedrock:*::foundation-model/*`,
        `arn:${this.partition}:bedrock:*:${this.account}:inference-profile/*`,
      ],
    }));

    // 普通の取得で読めなかったページを AgentCore Browser（AWS が用意する標準のブラウザ）で開く
    this.worker.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:StartBrowserSession', 'bedrock-agentcore:StopBrowserSession',
        'bedrock-agentcore:GetBrowserSession', 'bedrock-agentcore:ConnectBrowserAutomationStream'],
      resources: [`arn:${this.partition}:bedrock-agentcore:${this.region}:aws:browser/aws.browser.v1`],
    }));

    new cdk.CfnOutput(this, 'BucketName', { value: this.bucket.bucketName });
    new cdk.CfnOutput(this, 'WorkerName', { value: this.worker.functionName });
  }
}
