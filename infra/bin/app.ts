import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as cdk from 'aws-cdk-lib';
import { AgentStack } from '../lib/agent-stack.js';
import { EdgeStack } from '../lib/edge-stack.js';
import { EntryStack } from '../lib/entry-stack.js';
import { KnowledgeStack } from '../lib/knowledge-stack.js';
import { WatchStack } from '../lib/watch-stack.js';
import { WebStack } from '../lib/web-stack.js';

// デプロイ先は AWS_PROFILE のアカウントの us-west-2（cdkd が CDK_DEFAULT_ACCOUNT に入れる）
const account = process.env.CDK_DEFAULT_ACCOUNT;
if (!account) throw new Error('CDK_DEFAULT_ACCOUNT が空です。AWS_PROFILE を指定して cdkd を実行してください');
const env = { account, region: 'us-west-2' };
const app = new cdk.App();
cdk.Tags.of(app).add('Project', 'minorun-agent');

const knowledge = new KnowledgeStack(app, 'MinorunKnowledge', { env });
const agent = new AgentStack(app, 'MinorunAgent', {
  env,
  toolsGateway: { gatewayArn: knowledge.toolsGateway.attrGatewayArn, mcpUrl: knowledge.toolsGateway.attrGatewayUrl },
  knowledgeBucketName: `minorun-agent-knowledge-${env.account}`,
});
agent.addDependency(knowledge);
const entry = new EntryStack(app, 'MinorunEntry', { env, runtime: agent.runtime, legacyRuntime: agent.legacyRuntime, runtimeV2: agent.runtimeV2, conversationLog: agent.conversationLog });

// CloudFront の WAF はバージニア北部にしか置けない
new EdgeStack(app, 'MinorunEdge', { env: { account: env.account, region: 'us-east-1' } });

// WAF と独自ドメインの証明書は us-east-1 にあり、cdkd はリージョンをまたぐ参照を持てないので、
// infra/scripts/domain.py が ARN を infra/.edge.json に書き、ここで読む（無ければ WAF・独自ドメインなしで配る）
const edgeFile = new URL('../.edge.json', import.meta.url);
const edge = fs.existsSync(edgeFile) ? JSON.parse(fs.readFileSync(edgeFile, 'utf-8')) : undefined;
// スマホ向けの利用量ページの合言葉。初回に作って infra/.watch-token に置く（gitignore）
const tokenFile = new URL('../.watch-token', import.meta.url);
if (!fs.existsSync(tokenFile)) fs.writeFileSync(tokenFile, crypto.randomBytes(18).toString('base64url') + '\n');
const watch = new WatchStack(app, 'MinorunWatch', {
  env, agent, entry, knowledge,
  budgetEmail: process.env.ALERT_EMAIL ?? 'alerts@example.com',  // 予算とアラームの送り先
  dailyBudgetUsd: 30,
  alertUsd: 20,
  adminEmail: process.env.ADMIN_EMAIL ?? 'admin@example.com',  // 利用量ページにログインできる1人
  adminBaseUrl: `https://${edge?.domain ?? 'agent.minoruonda.com'}/admin/`,
  pageToken: fs.readFileSync(tokenFile, 'utf-8').trim(),
});
const web = new WebStack(app, 'MinorunWeb', { env, entry, edge, watch });
web.addDependency(watch);


