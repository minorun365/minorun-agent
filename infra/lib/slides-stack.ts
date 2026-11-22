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
// エージェントのツール make_slides が回数の表で数えてからスライド作成機能（AgentCore Runtime）をジョブごとのセッションで起こし、
// できあがったビューア（s/<ジョブ>/）は CloudFront の /s/* から誰でも開ける（URL を知る人だけ・検索には出さない）。
export class SlidesStack extends cdk.Stack {
  readonly bucket: s3.Bucket;
  readonly quota: dynamodb.Table;
  readonly worker: agentcore.CfnRuntime;
  readonly workerLogGroup: string;
  readonly harness: agentcore.CfnHarness;
  readonly staging?: agentcore.CfnRuntime;

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

    // Lambda のころ（2026-11-15）のスライド作成機能のログ。監視ページが過去の分を読むので、Lambda を外しても残す
    new logs.LogGroup(this, 'WorkerLogs', {
      logGroupName: '/aws/lambda/minorun-agent-slides',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // スライド作成機能は AgentCore Runtime（2026-11-16 に Lambda から移した。回答のエージェントと同じ実行基盤にそろえる）。
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
    // Harness のエージェントが書き上げたもの（work/<ジョブ>/）を受け取って消す
    this.bucket.grantReadWrite(workerRole, 'work/*');
    this.bucket.grantDelete(workerRole, 'work/*');
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'], resources: [this.bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': ['work/*'] } },
    }));
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

    // ───── スライドを書くエージェント（AgentCore Harness。2026-11-21）─────
    // スライド作成と同じイメージの入れ物で、エージェントがシェルとファイルの道具を使い、書く→焼く→測る→画像を見て直すを自分で回す。
    // シェルにこのロールの権限がそのまま入るので、渡すのはモデルの呼び出し・Claude API のキーの読み出し・受け渡しの置き場への書き込みだけ
    const vault = `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`;
    const apiKeyName = 'claude-api';  // AgentCore Identity の API キー資格情報（Claude Console でスライド作成用に発行したキー）
    const apiKeyArn = `${vault}/apikeycredentialprovider/${apiKeyName}`;
    const harnessRole = new iam.Role(this, 'HarnessRole', {
      roleName: 'minorun-agent-slides-harness',
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
    });
    harnessRole.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    image.repository.grantPull(harnessRole);
    harnessRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams', 'logs:DescribeLogGroups'],
      resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`],
    }));
    harnessRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:${this.partition}:bedrock:*::foundation-model/anthropic.claude-opus-5-5*`,
        `arn:${this.partition}:bedrock:*:${this.account}:inference-profile/global.anthropic.claude-opus-5-5*`,
      ],
    }));
    harnessRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:GetResourceApiKey', 'bedrock-agentcore:GetWorkloadAccessToken'],
      resources: [vault, apiKeyArn,
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/*`],
    }));
    harnessRole.addToPolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [`arn:${this.partition}:secretsmanager:${this.region}:${this.account}:secret:bedrock-agentcore-identity!default/apikey/${apiKeyName}-*`],
    }));
    this.bucket.grantPut(harnessRole, 'work/*');
    // 挿絵（いらすとやの画像一式）。規約上リポジトリに入れず、ここに置いた絵を入れ物が使うときに取り込む（slides/bin/illust.py）
    for (const role of [harnessRole, workerRole]) {
      this.bucket.grantRead(role, 'ill/*');
      role.addToPolicy(new iam.PolicyStatement({
        actions: ['s3:ListBucket'], resources: [this.bucket.bucketArn],
        conditions: { StringLike: { 's3:prefix': ['ill/*'] } },
      }));
    }
    this.harness = new agentcore.CfnHarness(this, 'Harness', {
      harnessName: 'minorun_agent_slides_harness',
      executionRoleArn: harnessRole.roleArn,
      environmentArtifact: { containerConfiguration: { containerUri: image.imageUri } },
      environment: { agentCoreRuntimeEnvironment: {
        lifecycleConfiguration: { idleRuntimeSessionTimeout: 120, maxLifetime: 3600 },
        networkConfiguration: { networkMode: 'PUBLIC' },
      } },
      // 既定は Bedrock の Opus。スライド作成機能が呼ぶたびに、今月のクレジットの残りを見て Claude API（API キー）か Bedrock を指定し直す
      model: { bedrockModelConfig: { modelId: 'global.anthropic.claude-opus-5-5', maxTokens: 32000 } },
      // 記事の本文と頼んだ人の一言を残さない（既定ではマネージドのメモリに会話が残る）
      memory: { disabled: {} },
      skills: [{ path: '/app/skills/illustrations' }, { path: '/app/skills/aws-diagrams' }],
      // 外へ出る道具（web_fetch）と子エージェントは渡さない
      allowedTools: ['shell', 'read', 'write', 'edit', 'todo_write', 'retrieve_context'],
      maxIterations: 80,
      maxTokens: 32000,
      timeoutSeconds: 1800,
      environmentVariables: { SLIDES_BUCKET: this.bucket.bucketName },
    });
    this.harness.node.addDependency(harnessRole);
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeHarness'],
      resources: [this.harness.attrArn, `${this.harness.attrArn}/*`],
    }));
    // InvokeHarness は、Harness の ARN に対する InvokeAgentRuntime の権限も見る（2026-11-21 の通しテストで AccessDenied になって分かった）。
    // 下で動く Runtime の ARN も念のため並べる
    const harnessRuntime = `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:runtime/${this.harness.attrEnvironmentAgentCoreRuntimeEnvironmentAgentRuntimeId}`;
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime'],
      resources: [this.harness.attrArn, `${this.harness.attrArn}/*`, harnessRuntime, `${harnessRuntime}/*`],
    }));
    // クレジットの見張り：回答が Claude API で払った額を会話の表から足し、キーの有無を確かめる
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['dynamodb:Scan'],
      resources: [`arn:${this.partition}:dynamodb:${this.region}:${this.account}:table/minorun-agent-conversations`],
    }));
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem'], resources: [this.quota.tableArn],
    }));
    workerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:GetApiKeyCredentialProvider'], resources: [vault, apiKeyArn],
    }));
    const workerEnv = {
      SLIDES_BUCKET: this.bucket.bucketName,
      SITE_URL: props.siteUrl,
      // 作れなかった1本を回数から戻すときに使う。クレジットの台帳もこの表に置く
      SLIDES_TABLE: this.quota.tableName,
      // 2026-11-15 みのるん決定（賢い方の Opus）。Bedrock の利用規約の同意は同日に済ませた
      SLIDES_MODEL: 'global.anthropic.claude-opus-5-5',
      SLIDES_EFFORT: 'medium',
      // Harness を使えなかったときの書き方（1回書かせて測る）は、回答と同じ ID 連携で Claude API を先に使う（2026-11-15 みのるん指示）
      ANTHROPIC_PRIMARY: 'on',
      SLIDES_API_MODEL: 'claude-opus-5-5',
      // Claude Console の Workload identity で作った ID 連携（回答と同じルール）。秘密の値ではない
      CLAUDE_WIF_RULE_ID: process.env.CLAUDE_WIF_RULE_ID ?? '',
      CLAUDE_WIF_ORGANIZATION_ID: process.env.CLAUDE_WIF_ORGANIZATION_ID ?? '',
      CLAUDE_WIF_SERVICE_ACCOUNT_ID: process.env.CLAUDE_WIF_SERVICE_ACCOUNT_ID ?? '',
      HARNESS_ARN: this.harness.attrArn,
      CLAUDE_API_KEY_ARN: apiKeyArn,
      CONVERSATION_TABLE: 'minorun-agent-conversations',
      // クレジット（月 $100）のうち、ここまで使ったら Bedrock へ回す線（2026-11-21 みのるん承認の $90）。クレジットが戻る日も合わせる
      CREDIT_LIMIT_USD: '90',
      CREDIT_RESET_DAY: process.env.CREDIT_RESET_DAY ?? '3',  // Max の API クレジットは毎月3日 9:00 JST に切り替わる（Console の有効期限で確認。2026-11-22）
      SLIDES_JOB_ESTIMATE_USD: '3',
    };
    // 本番のスライド作成機能を切り替える前に、新しいイメージを試験用の Runtime で通しテストする（SLIDES_STAGING=1 のときだけ作る）。
    // そのあいだ本番のスライド作成機能は SLIDES_PROD_IMAGE_URI のイメージ（切り替え前のもの）のまま動かす
    if (process.env.SLIDES_STAGING === '1') {
      this.staging = new agentcore.CfnRuntime(this, 'WorkerStaging', {
        agentRuntimeName: 'minorun_agent_slides_staging',
        description: 'スライド作成機能の切り替え前の通しテスト用',
        agentRuntimeArtifact: { containerConfiguration: { containerUri: image.imageUri } },
        networkConfiguration: { networkMode: 'PUBLIC' },
        protocolConfiguration: 'HTTP',
        roleArn: workerRole.roleArn,
        lifecycleConfiguration: { idleRuntimeSessionTimeout: 60, maxLifetime: 3600 },
        environmentVariables: workerEnv,
      });
      this.staging.node.addDependency(workerRole);
      new cdk.CfnOutput(this, 'StagingArn', { value: this.staging.attrAgentRuntimeArn });
    }
    this.worker = new agentcore.CfnRuntime(this, 'Worker', {
      agentRuntimeName: 'minorun_agent_slides',
      description: 'URL の記事やテーマから minorun-glass のスライドを作ってビューアを公開する',
      agentRuntimeArtifact: { containerConfiguration: { containerUri: process.env.SLIDES_PROD_IMAGE_URI || image.imageUri } },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      roleArn: workerRole.roleArn,
      // 焼き終えたら60秒で畳む（待っている間のメモリ代を払わない）。1本が60分を超えたら打ち切る（Harness が見て直すぶん、20分から延ばした）
      lifecycleConfiguration: { idleRuntimeSessionTimeout: 60, maxLifetime: 3600 },
      environmentVariables: workerEnv,
    });
    this.worker.node.addDependency(workerRole);
    // スライド作成機能のログ（stdout）は AgentCore が自分で作るこのロググループに出る。監視ページの「スライド」タブが読む
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

    // Harness のログには、やり取りの全文（記事の本文・頼んだ人の一言・エージェントの作業）が残る。調べものに要る1週間だけ持つ
    const harnessLogGroup = `/aws/bedrock-agentcore/runtimes/${this.harness.attrEnvironmentAgentCoreRuntimeEnvironmentAgentRuntimeId}-DEFAULT`;
    const harnessRetention = {
      service: 'CloudWatchLogs',
      action: 'putRetentionPolicy',
      parameters: { logGroupName: harnessLogGroup, retentionInDays: logs.RetentionDays.ONE_WEEK },
      physicalResourceId: cr.PhysicalResourceId.of(harnessLogGroup),
    };
    new cr.AwsCustomResource(this, 'HarnessLogRetention', {
      onCreate: harnessRetention,
      onUpdate: harnessRetention,
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
