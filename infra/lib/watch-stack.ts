import * as path from 'node:path';
import * as fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as cdk from 'aws-cdk-lib';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as cw from 'aws-cdk-lib/aws-cloudwatch';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import type { Construct } from 'constructs';
import type { AgentStack } from './agent-stack.js';
import type { EntryStack } from './entry-stack.js';
import type { KnowledgeStack } from './knowledge-stack.js';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const authFile = path.join(currentDir, '../.auth.json');
const appAuth = fs.existsSync(authFile) ? JSON.parse(fs.readFileSync(authFile, 'utf-8')) : undefined;
const NS = 'MinorunAgent';
const EVAL_NS = 'Bedrock-AgentCore/Evaluations';

export interface WatchStackProps extends cdk.StackProps {
  readonly agent: AgentStack;
  readonly entry: EntryStack;
  readonly knowledge: KnowledgeStack;
  readonly budgetEmail: string;
  // 1日の予算（ドル）。クレジットを除いた額で見る
  readonly dailyBudgetUsd: number;
  // スマホ向けの利用量ページの合言葉（infra/.watch-token。app.ts が初回に作る）
  readonly pageToken: string;
  // 概算コストがこの額を超えたらメールで知らせる（予算の手前）
  readonly alertUsd: number;
  // 利用量ページ（HTML）にログインできる唯一のメールアドレス
  readonly adminEmail: string;
  // 利用量ページの公開URL（CloudFront の /admin/ 。ログインの戻り先もここ）
  readonly adminBaseUrl: string;
}

// 見張り：ダッシュボード、断った数とエラーのメトリクス、予算、常時のオンライン評価。
// 本体と切り離しておき、見張りを変えてもエージェントを作り直さない。
export class WatchStack extends cdk.Stack {
  // 利用量ページの関数URL（MinorunWeb の /admin* がここへ中継する）
  readonly usagePageUrl: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: WatchStackProps) {
    super(scope, id, props);
    const runtimeArn = props.agent.runtime.attrAgentRuntimeArn;
    const runtimeLogGroup = `/aws/bedrock-agentcore/runtimes/${props.agent.runtime.attrAgentRuntimeId}-DEFAULT`;

    // 1問ごとの「最初の文字までの時間」と「答え終わるまでの時間」（1問1行の表のストリームから）
    const turnLogs = new logs.LogGroup(this, 'TurnMetricsLogs', {
      logGroupName: '/aws/lambda/minorun-agent-turn-metrics',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const turnMetrics = new lambda.Function(this, 'TurnMetrics', {
      functionName: 'minorun-agent-turn-metrics',
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'turn_metrics.handler',
      code: lambda.Code.fromAsset(path.join(currentDir, '../lambda/turn-metrics'), { exclude: ['**/__pycache__', '**/*.pyc'] }),
      timeout: cdk.Duration.seconds(30),
      memorySize: 128,
      logGroup: turnLogs,
    });
    turnMetrics.addEventSource(new sources.DynamoEventSource(props.agent.conversationLog, {
      startingPosition: lambda.StartingPosition.LATEST,
      batchSize: 100,
      maxBatchingWindow: cdk.Duration.seconds(10),
      retryAttempts: 2,
      filters: [lambda.FilterCriteria.filter({ eventName: lambda.FilterRule.isEqual('INSERT') })],
    }));

    // 確認役の判定（{"decision": "reject", "reason": ...}）とエージェントの失敗（{"agent_error": ...}）
    new logs.MetricFilter(this, 'RejectedFilter', {
      logGroup: logs.LogGroup.fromLogGroupName(this, 'InterceptorLogs', '/aws/lambda/minorun-agent-interceptor'),
      filterPattern: logs.FilterPattern.stringValue('$.decision', '=', 'reject'),
      metricNamespace: NS, metricName: 'Rejected', metricValue: '1',
      dimensions: { reason: '$.reason' },
    });
    new logs.MetricFilter(this, 'PassedFilter', {
      logGroup: logs.LogGroup.fromLogGroupName(this, 'InterceptorLogs2', '/aws/lambda/minorun-agent-interceptor'),
      filterPattern: logs.FilterPattern.stringValue('$.decision', '=', 'pass'),
      metricNamespace: NS, metricName: 'Passed', metricValue: '1',
    });
    for (const [id, runtime] of [['AgentErrorFilter', props.agent.legacyRuntime], ['AgentErrorV2Filter', props.agent.runtimeV2]] as const) {
      new logs.MetricFilter(this, id, {
        logGroup: logs.LogGroup.fromLogGroupName(this, `${id}Logs`, `/aws/bedrock-agentcore/runtimes/${runtime.attrAgentRuntimeId}-DEFAULT`),
        filterPattern: logs.FilterPattern.exists('$.agent_error'),
        metricNamespace: NS, metricName: 'AgentErrors', metricValue: '1',
      });

    }

    // オンライン評価（2026-10-25 決定：全セッション。同日に停止）。結果は専用のロググループと Bedrock-AgentCore/Evaluations へ
    // （名前空間を変える metricsNamespace は API にはあるが CloudFormation の型にまだ無い）
    const evalRole = new iam.Role(this, 'EvaluationRole', {
      roleName: 'minorun-agent-evaluation',
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account, 'aws:ResourceAccount': this.account },
          ArnLike: { 'aws:SourceArn': [
            `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:evaluator/*`,
            `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:online-evaluation-config/*`,
          ] },
        },
      }),
    });
    evalRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:DescribeLogGroups', 'logs:GetQueryResults', 'logs:StartQuery'],
      resources: ['*'],
    }));
    evalRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/evaluations/*`],
    }));
    evalRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:DescribeIndexPolicies', 'logs:PutIndexPolicy'],
      resources: [
        `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:aws/spans`,
        `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:aws/spans:*`,
      ],
    }));
    evalRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:${this.partition}:bedrock:*::foundation-model/*`,
        `arn:${this.partition}:bedrock:*:${this.account}:inference-profile/*`,
      ],
    }));
    // 評価の観点は4つ：役に立つか、調べた結果に沿っているか（でっち上げないか）、
    // 指示（短さ・出典の付け方）を守ったか、道具の選び方。評価の料金は観点の数に比例する
    const evaluators = ['Helpfulness', 'Faithfulness', 'InstructionFollowing', 'ToolSelectionAccuracy'];
    const onlineEval = new agentcore.CfnOnlineEvaluationConfig(this, 'OnlineEvaluation', {
      onlineEvaluationConfigName: 'minorun_agent_quality',
      description: 'みのるんエージェントの常時評価（全セッション）',
      evaluationExecutionRoleArn: evalRole.roleArn,
      evaluators: evaluators.map((e) => ({ evaluatorId: `Builtin.${e}` })),
      dataSourceConfig: { cloudWatchLogs: {
        logGroupNames: [runtimeLogGroup],
        serviceNames: ['minorun_agent.DEFAULT'],
      } },
      rule: {
        samplingConfig: { samplingPercentage: 100 },
        // 会話が5分止まったら1セッションとして締めて評価する
        sessionConfig: { sessionTimeoutMinutes: 5 },
      },
      // 2026-10-25 費用を抑えるため止めた（みのるん指示）。再開するときは ENABLED に戻す
      executionStatus: 'DISABLED',
    });
    onlineEval.node.addDependency(evalRole);

    // 予算：クレジットを除いた1日の費用（アカウント全体。設計の記録「1日$30」）。
    // クレジットが付いたアカウントでは、含めると常に0ドルに見えて鳴らない
    new budgets.CfnBudget(this, 'DailyBudget', {
      budget: {
        budgetName: 'minorun-agent-daily',
        budgetType: 'COST',
        timeUnit: 'DAILY',
        budgetLimit: { amount: props.dailyBudgetUsd, unit: 'USD' },
        costTypes: { includeCredit: false, includeRefund: false },
      },
      // 費用メールは下の概算アラームへ集約し、予算からの重複通知を止める。
      notificationsWithSubscribers: [],
    });

    // スマホ向けの1枚：今日の質問数・利用者数・概算コスト。関数URL＋合言葉（?k=）で開く。
    // 15分おきにも動いて概算コストを MinorunAgent/EstimatedCostUsd に出し、手前の額でアラームが鳴る
    const pageLogs = new logs.LogGroup(this, 'UsagePageLogs', {
      logGroupName: '/aws/lambda/minorun-agent-usage-page',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // 利用量ページのログイン：Cognito のマネージドログイン（メールのワンタイムコードかパスキー）。
    // 自分で登録できない設定にし、ADMIN_EMAIL の1人だけを管理者が作る（infra/scripts/admin_user.py）。
    // ページ側でもメールアドレスを照合するので、もし別の人が入れても中身は見えない
    const adminPool = new cognito.UserPool(this, 'AdminPool', {
      userPoolName: 'minorun-agent-admin',
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      mfa: cognito.Mfa.OFF,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      signInCaseSensitive: false,
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: false } },
      accountRecovery: cognito.AccountRecovery.NONE,
      signInPolicy: { allowedFirstAuthFactors: { password: true, passkey: true, emailOtp: true } },
      passkeyUserVerification: cognito.PasskeyUserVerification.PREFERRED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const adminDomain = adminPool.addDomain('AdminDomain', {
      cognitoDomain: { domainPrefix: 'minorun-agent-admin' },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });
    const adminClient = adminPool.addClient('AdminClient', {
      userPoolClientName: 'usage-page',
      generateSecret: false,
      preventUserExistenceErrors: true,
      authFlows: { user: true },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
        callbackUrls: [`${props.adminBaseUrl}callback`],
        logoutUrls: [props.adminBaseUrl],
      },
      idTokenValidity: cdk.Duration.minutes(60),
      accessTokenValidity: cdk.Duration.minutes(60),
      refreshTokenValidity: cdk.Duration.days(30),
    });
    const usagePage = new lambda.Function(this, 'UsagePage', {
      functionName: 'minorun-agent-usage-page',
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'usage_page.handler',
      code: lambda.Code.fromAsset(path.join(currentDir, '../lambda/usage-page'), { exclude: ['**/__pycache__', '**/*.pyc'] }),
      timeout: cdk.Duration.seconds(20),
      memorySize: 256,
      logGroup: pageLogs,
      environment: {
        PAGE_TOKEN: props.pageToken,
        LOG_TABLE: 'minorun-agent-conversations',
        ERROR_TABLE: 'minorun-agent-transport-errors',
        RUNTIME_ARN: runtimeArn,
        RUNTIME_ARNS: cdk.Fn.join(',', [props.agent.legacyRuntime.attrAgentRuntimeArn, props.agent.runtimeV2.attrAgentRuntimeArn]),
        TOOLS_GATEWAY_ARN: props.knowledge.toolsGateway.attrGatewayArn,
        ALERT_USD: String(props.alertUsd),
        YEN_PER_USD: '150',
        ADMIN_EMAIL: props.adminEmail,
        USER_POOL_ID: adminPool.userPoolId,
        ...(appAuth ? { APP_USER_POOL_ID: appAuth.userPoolId } : {}),
        AUTH_DOMAIN: adminDomain.baseUrl(),
        CLIENT_ID: adminClient.userPoolClientId,
        PUBLIC_BASE: props.adminBaseUrl,
      },
    });
    // 表は名前で指す（MinorunAgent からの参照を増やすと、そちらを出し直すまでこのスタックを出せない）
    dynamodb.Table.fromTableName(this, 'ConversationLogByName', 'minorun-agent-conversations').grant(usagePage, 'dynamodb:Scan');
    dynamodb.Table.fromTableName(this, 'TransportErrorsByName', 'minorun-agent-transport-errors').grant(usagePage, 'dynamodb:Query');
    usagePage.addToRolePolicy(new iam.PolicyStatement({ actions: ['cloudwatch:GetMetricData'], resources: ['*'] }));
    usagePage.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem'],
      resources: [this.formatArn({ service: 'dynamodb', resource: 'table', resourceName: 'minorun-agent-usage' })],
      conditions: { 'ForAllValues:StringEquals': { 'dynamodb:LeadingKeys': ['audience#owner'] } },
    }));
    // 確認した停止記録だけに既読時刻を保存する。
    usagePage.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:UpdateItem'],
      resources: [this.formatArn({ service: 'dynamodb', resource: 'table', resourceName: 'minorun-agent-usage' })],
      conditions: {
        'ForAllValues:StringLike': { 'dynamodb:LeadingKeys': ['ban#a#*', 'ban#u#*'] },
        'ForAllValues:StringEquals': { 'dynamodb:Attributes': ['pk', 'at', 'reviewed_at'] },
      },
    }));
    // 利用停止の一覧（pk=ban#…）を読む。Scan は先頭キーで絞れないので、この表の Scan だけを足す
    usagePage.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:Scan'],
      resources: [this.formatArn({ service: 'dynamodb', resource: 'table', resourceName: 'minorun-agent-usage' })],
    }));
    if (appAuth) usagePage.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cognito-idp:ListUsers'],
      resources: [this.formatArn({ service: 'cognito-idp', resource: 'userpool', resourceName: appAuth.userPoolId })],
    }));
    // 外部のダッシュボード（USAGE_PAGE_ORIGIN）がブラウザから直接 JSON を読む（合言葉 ?k= はこれまでどおり必須）
    const pageUrl = usagePage.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      cors: { allowedOrigins: [process.env.USAGE_PAGE_ORIGIN ?? 'https://dashboard.example.com'], allowedMethods: [lambda.HttpMethod.GET], maxAge: cdk.Duration.hours(1) },
    });
    this.usagePageUrl = pageUrl;
    new cognito.CfnManagedLoginBranding(this, 'AdminLoginBranding', {
      userPoolId: adminPool.userPoolId,
      clientId: adminClient.userPoolClientId,
      useCognitoProvidedValues: true,
    });
    new events.Rule(this, 'UsagePageSchedule', {
      schedule: events.Schedule.rate(cdk.Duration.minutes(15)),
      targets: [new targets.LambdaFunction(usagePage, { retryAttempts: 0 })],
    });
    const alertTopic = new sns.Topic(this, 'CostAlertTopic', { topicName: 'minorun-agent-cost-alert' });
    alertTopic.addSubscription(new subs.EmailSubscription(props.budgetEmail));
    const costAlarm = new cw.Alarm(this, 'EstimatedCostAlarm', {
      alarmName: 'minorun-agent-estimated-cost',  // 名前を変えると作り直しになるので英字のまま
      alarmDescription: `日本時間の当日利用額が1万円超（参考換算1ドル150円・概算）`,
      metric: new cw.Metric({ namespace: NS, metricName: 'EstimatedCostUsd', statistic: 'Maximum', period: cdk.Duration.minutes(15) }),
      threshold: props.alertUsd,
      comparisonOperator: cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cw.TreatMissingData.IGNORE,
    });
    costAlarm.addAlarmAction(new cwActions.SnsAction(alertTopic));
    const rollingCostAlarm = new cw.Alarm(this, 'RollingCostAlarm', {
      alarmName: 'minorun-agent-rolling-30d-cost',
      alarmDescription: '直近30日間の利用額が3万円超（予測ではなく積算・参考換算1ドル150円・概算）',
      metric: new cw.Metric({ namespace: NS, metricName: 'Rolling30DayCostUsd', statistic: 'Maximum', period: cdk.Duration.minutes(15) }),
      threshold: 30000 / 150,
      comparisonOperator: cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cw.TreatMissingData.IGNORE,
    });
    rollingCostAlarm.addAlarmAction(new cwActions.SnsAction(alertTopic));


    // 保存の瞬間の仕分け（2026-10-25、リアルタイム見張りの案をみのるん承認）：新しい1行ごとに Haiku 5.5 に1回聞き、
    // flag と理由を同じ行へ書き戻す。injection・personal_info は SNS へ（15分に1通へまとめる）
    const flagLogs = new logs.LogGroup(this, 'TurnFlagLogs', {
      logGroupName: '/aws/lambda/minorun-agent-turn-flag',
      retention: logs.RetentionDays.SIX_MONTHS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const haikuProfile = 'global.anthropic.claude-haiku-5-5';
    const turnFlag = new lambda.Function(this, 'TurnFlag', {
      functionName: 'minorun-agent-turn-flag',
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'turn_flag.handler',
      code: lambda.Code.fromAsset(path.join(currentDir, '../lambda/turn-flag'), { exclude: ['**/__pycache__', '**/*.pyc'] }),
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      logGroup: flagLogs,
      environment: {
        MODEL_ID: haikuProfile,
        LOG_TABLE: 'minorun-agent-conversations',
        STATE_TABLE: 'minorun-agent-usage',
        ADMIN_BASE_URL: props.adminBaseUrl,
        TOPIC_ARN: alertTopic.topicArn,
      },
    });
    // INSERT だけを受ける。書き戻しは MODIFY なので、ここへは戻ってこない（無限に回らない）
    turnFlag.addEventSource(new sources.DynamoEventSource(props.agent.conversationLog, {
      startingPosition: lambda.StartingPosition.LATEST,
      batchSize: 10,
      retryAttempts: 2,
      filters: [lambda.FilterCriteria.filter({ eventName: lambda.FilterRule.isEqual('INSERT') })],
    }));
    dynamodb.Table.fromTableName(this, 'ConversationLogForFlag', 'minorun-agent-conversations').grant(turnFlag, 'dynamodb:UpdateItem', 'dynamodb:GetItem');
    dynamodb.Table.fromTableName(this, 'UsageTableForFlag', 'minorun-agent-usage').grant(turnFlag, 'dynamodb:GetItem', 'dynamodb:UpdateItem', 'dynamodb:PutItem');
    alertTopic.grantPublish(turnFlag);
    // Bedrock は Haiku 5.5 の global の推論プロファイル（と、その行き先のモデル）だけ
    turnFlag.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:${this.partition}:bedrock:${this.region}:${this.account}:inference-profile/${haikuProfile}`,
        `arn:${this.partition}:bedrock:::foundation-model/anthropic.claude-haiku-5-5`,
        `arn:${this.partition}:bedrock:*::foundation-model/anthropic.claude-haiku-5-5`,
      ],
    }));
    new events.Rule(this, 'TurnFlagFlush', {
      schedule: events.Schedule.rate(cdk.Duration.minutes(15)),
      targets: [new targets.LambdaFunction(turnFlag, { retryAttempts: 0 })],
    });

    // 致命的なエラーのメール（2026-10-25、先行公開の開始に合わせて）。メールの件名はアラーム名がそのまま入るので日本語にする
    const alarm = (id: string, name: string, description: string, props2: Omit<cw.AlarmProps, 'alarmName' | 'alarmDescription'>) => {
      const a2 = new cw.Alarm(this, id, { alarmName: `みのるんエージェント｜${name}`, alarmDescription: description, ...props2 });
      a2.addAlarmAction(new cwActions.SnsAction(alertTopic));
      a2.addOkAction(new cwActions.SnsAction(alertTopic));
      return a2;
    };
    const fiveMin = cdk.Duration.minutes(5);
    const gte = cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD;
    alarm('AgentErrorsAlarm', 'エージェントの失敗が続いている', '5分間にエージェントの失敗（Runtime のログの agent_error）が3件以上。Runtime のログで agent_error を探す', {
      metric: new cw.Metric({ namespace: NS, metricName: 'AgentErrors', statistic: 'Sum', period: fiveMin }),
      threshold: 3, comparisonOperator: gte, evaluationPeriods: 1, treatMissingData: cw.TreatMissingData.NOT_BREACHING,
    });
    alarm('EntryErrorsAlarm', '入口で5xxが出ている', '5分間に入口の Gateway の内部エラー（5xx）が5件以上。Runtime が落ちている・確認役が応答しない、などを疑う', {
      metric: new cw.Metric({
        namespace: 'AWS/Bedrock-AgentCore', metricName: 'SystemErrors', statistic: 'Sum', period: fiveMin,
        dimensionsMap: { Resource: props.entry.gateway.attrGatewayArn, Operation: 'InvokeGateway', Protocol: 'HTTP' },
      }),
      threshold: 5, comparisonOperator: gte, evaluationPeriods: 1, treatMissingData: cw.TreatMissingData.NOT_BREACHING,
    });
    const interceptorMetric = (metricName: string) => new cw.Metric({
      namespace: 'AWS/Lambda', metricName, statistic: 'Sum', period: fiveMin,
      dimensionsMap: { FunctionName: 'minorun-agent-interceptor' },
    });
    alarm('InterceptorFailAlarm', '確認役のLambdaが失敗している', '確認役（minorun-agent-interceptor）のエラーかスロットルが5分間に1件以上。全員の質問が断られている可能性がある', {
      metric: new cw.MathExpression({
        expression: 'FILL(e, 0) + FILL(t, 0)', label: 'エラーとスロットル', period: fiveMin,
        usingMetrics: { e: interceptorMetric('Errors'), t: interceptorMetric('Throttles') },
      }),
      threshold: 1, comparisonOperator: gte, evaluationPeriods: 1, treatMissingData: cw.TreatMissingData.NOT_BREACHING,
    });
    // 日本時間の1〜6時台（UTC の16〜21時台）は、質問が無くても鳴らさないよう 1 に置き換える
    alarm('NoQuestionsAlarm', '1時間質問が通っていない', '確認役を通った質問が1時間0件（日本時間の1〜6時台は除く）。画面か入口が止まっていないか確かめる', {
      metric: new cw.MathExpression({
        expression: 'IF(HOUR(f) >= 16 AND HOUR(f) <= 21, 1, f)', label: '通った質問（夜間は1）', period: cdk.Duration.hours(1),
        usingMetrics: {
          f: new cw.MathExpression({
            expression: 'FILL(p, 0)', period: cdk.Duration.hours(1),
            usingMetrics: { p: new cw.Metric({ namespace: NS, metricName: 'Passed', statistic: 'Sum', period: cdk.Duration.hours(1) }) },
          }),
        },
      }),
      threshold: 1, comparisonOperator: cw.ComparisonOperator.LESS_THAN_THRESHOLD, evaluationPeriods: 1,
      treatMissingData: cw.TreatMissingData.BREACHING,
    });

    // ダッシュボード（1時間ごと）
    const hour = cdk.Duration.hours(1);
    const m = (metricName: string, statistic = 'Sum', label?: string) =>
      new cw.Metric({ namespace: NS, metricName, statistic, period: hour, label });
    const rt = (metricName: string, label: string) => new cw.Metric({
      namespace: 'AWS/Bedrock-AgentCore', metricName, statistic: 'Sum', period: hour, label,
      dimensionsMap: { Resource: runtimeArn, Operation: 'InvokeAgentRuntime', Name: `${props.agent.runtime.agentRuntimeName}::DEFAULT` },
    });
    const tools = (metricName: string, label: string) => new cw.Metric({
      namespace: 'AWS/Bedrock-AgentCore', metricName, statistic: 'Sum', period: hour, label,
      dimensionsMap: {
        Resource: props.knowledge.toolsGateway.attrGatewayArn, Operation: 'InvokeGateway', Method: 'tools/call', Protocol: 'MCP',
      },
    });
    const tokens = (metricName: string, model: string, label: string) => new cw.Metric({
      namespace: 'AWS/Bedrock', metricName, statistic: 'Sum', period: hour, label,
      dimensionsMap: { ModelId: `global.anthropic.${model}` },
    });
    const waf = (rule: string, label: string) => new cw.Metric({
      namespace: 'AWS/WAFV2', metricName: 'BlockedRequests', statistic: 'Sum', period: hour, label, region: 'us-east-1',
      dimensionsMap: { WebACL: 'minorun-agent-web', Rule: rule },
    });
    const search = (expression: string, label: string) =>
      new cw.MathExpression({ expression, label, period: hour, usingMetrics: {} });

    new cw.Dashboard(this, 'Dashboard', {
      dashboardName: 'minorun-agent',
      defaultInterval: cdk.Duration.days(3),
      widgets: [
        [
          new cw.GraphWidget({
            title: '質問数と断った数（1時間ごと）', width: 12,
            left: [m('Questions', 'Sum', '答えた質問'), m('Passed', 'Sum', '確認役を通った')],
            right: [search(`SEARCH('{${NS},reason} MetricName="Rejected"', 'Sum', 3600)`, '確認役が断った')],
          }),
          new cw.GraphWidget({
            title: 'WAF が断った数（1時間ごと）', width: 12,
            left: [waf('api-post-per-ip', '質問の送りすぎ（IP）'), waf('all-per-ip', '読み込みすぎ（IP）'),
              waf('AWSManagedRulesCommonRuleSet', '基本の規則'), waf('AWSManagedRulesKnownBadInputsRuleSet', '既知の攻撃'),
              waf('AWSManagedRulesAmazonIpReputationList', '評判の悪い IP')],
          }),
        ],
        [
          new cw.GraphWidget({
            title: '最初の文字までの時間（ミリ秒）', width: 12,
            left: [m('FirstTextMs', 'p50', '中央値'), m('FirstTextMs', 'p90', '遅い1割'), m('FirstTextMs', 'Maximum', '最大')],
          }),
          new cw.GraphWidget({
            title: '答え終わるまでの時間（ミリ秒）', width: 12,
            left: [m('TotalMs', 'p50', '中央値'), m('TotalMs', 'p90', '遅い1割')],
          }),
        ],
        [
          new cw.GraphWidget({
            title: 'エラー（1時間ごと）', width: 12,
            left: [m('AgentErrors', 'Sum', 'エージェントの失敗'), rt('SystemErrors', 'Runtime の内部エラー'),
              rt('UserErrors', 'Runtime の呼び出しエラー'), rt('Throttles', 'Runtime の混雑')],
          }),
          new cw.GraphWidget({
            title: '道具の呼び出しと失敗', width: 12,
            left: [tools('Invocations', '呼び出し')],
            right: [tools('SystemErrors', '内部エラー'), tools('UserErrors', '呼び出しエラー')],
          }),
        ],
        [
          new cw.GraphWidget({
            title: 'Bedrock のトークン数（アカウント全体。他のアプリも同じモデルを使うと混ざる）', width: 12,
            left: [tokens('InputTokenCount', 'claude-sonnet-5-5', 'Sonnet 入力'), tokens('InputTokenCount', 'claude-haiku-5-5', 'Haiku 入力')],
            right: [tokens('OutputTokenCount', 'claude-sonnet-5-5', 'Sonnet 出力'), tokens('OutputTokenCount', 'claude-haiku-5-5', 'Haiku 出力')],
          }),
          new cw.GraphWidget({
            title: '評価の点（オンライン評価・1時間の平均）', width: 12,
            left: [search(`SEARCH('{${EVAL_NS}} ', 'Average', 3600)`, '')],
          }),
        ],
        [
          new cw.LogQueryWidget({
            title: '直近の断った理由', width: 24, height: 6,
            logGroupNames: ['/aws/lambda/minorun-agent-interceptor'],
            queryLines: ['fields @timestamp, reason', 'filter decision = "reject"', 'stats count() as 件数 by reason', 'sort 件数 desc'],
          }),
        ],
      ],
    });

    new cdk.CfnOutput(this, 'DashboardUrl', {
      value: `https://${this.region}.console.aws.amazon.com/cloudwatch/home?region=${this.region}#dashboards/dashboard/minorun-agent`,
    });
    new cdk.CfnOutput(this, 'UsagePageUrl', { value: props.adminBaseUrl });
    new cdk.CfnOutput(this, 'UsageJsonUrl', { value: `${pageUrl.url}?k=${props.pageToken}&format=json` });
    new cdk.CfnOutput(this, 'AdminUserPoolId', { value: adminPool.userPoolId });
    new cdk.CfnOutput(this, 'OnlineEvaluationId', { value: onlineEval.attrOnlineEvaluationConfigId });
  }
}
