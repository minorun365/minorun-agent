import * as cdk from 'aws-cdk-lib';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import type { Construct } from 'constructs';

// CloudFront に付ける WAF（バージニア北部でしか作れないので、ここだけ us-east-1）。
// 断るのは手前ほど安い：IP の多さはここ、匿名IDごとの1分5件と1日30件は確認役、全体の1分60件は Gateway。
export const WEB_ACL_ARN_PARAM = '/minorun-agent/edge/web-acl-arn';

export class EdgeStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: cdk.StackProps) {
    super(scope, id, props);

    const visibility = (name: string) => ({
      cloudWatchMetricsEnabled: true, metricName: name, sampledRequestsEnabled: true,
    });
    // count だけにする規則：質問の本文に URL が入るのは普通なので、本文の URL を見る2つは止めない
    const managed = (priority: number, name: string, countOnly: string[] = []) => ({
      name, priority,
      statement: { managedRuleGroupStatement: {
        vendorName: 'AWS', name,
        ...(countOnly.length ? { ruleActionOverrides: countOnly.map((n) => ({ name: n, actionToUse: { count: {} } })) } : {}),
      } },
      overrideAction: { none: {} },
      visibilityConfig: visibility(name),
    });
    const isPost = { byteMatchStatement: {
      fieldToMatch: { method: {} }, positionalConstraint: 'EXACTLY', searchString: 'POST',
      textTransformations: [{ priority: 0, type: 'NONE' }],
    } };
    const isApi = { byteMatchStatement: {
      fieldToMatch: { uriPath: {} }, positionalConstraint: 'STARTS_WITH', searchString: '/api/',
      textTransformations: [{ priority: 0, type: 'NONE' }],
    } };
    // 断ったときも画面が日本語で案内できるよう、確認役と同じ形の JSON で 429 を返す
    const tooMany = { block: { customResponse: {
      responseCode: 429, customResponseBodyKey: 'too_many',
    } } };

    const acl = new wafv2.CfnWebACL(this, 'WebAcl', {
      name: 'minorun-agent-web',
      description: 'minorun-agent CloudFront',  // 日本語は使えない（英数字と一部の記号だけ）
      scope: 'CLOUDFRONT',
      defaultAction: { allow: {} },
      visibilityConfig: visibility('minorun-agent-web'),
      customResponseBodies: {
        too_many: {
          contentType: 'APPLICATION_JSON',
          content: JSON.stringify({ error: 'per_ip', message: '少し間をあけてから、もう一度送ってください。' }),
        },
      },
      rules: [
        // 質問の送信（/api/* への POST）：1つの IP から5分に100件まで（設計の記録の値）。
        // 1人は確認役で1分5件までなので、同じ回線を4人以上が全力で使わない限り当たらない
        {
          name: 'api-post-per-ip', priority: 0,
          statement: { rateBasedStatement: {
            limit: 100, evaluationWindowSec: 300, aggregateKeyType: 'IP',
            scopeDownStatement: { andStatement: { statements: [isPost, isApi] } },
          } },
          action: tooMany,
          visibilityConfig: visibility('api-post-per-ip'),
        },
        // 画面の読み込みも含めた全体：5分に2,000件まで（画面1回で画像や JS を十数件読む）
        {
          name: 'all-per-ip', priority: 1,
          statement: { rateBasedStatement: { limit: 2000, evaluationWindowSec: 300, aggregateKeyType: 'IP' } },
          action: tooMany,
          visibilityConfig: visibility('all-per-ip'),
        },
        managed(10, 'AWSManagedRulesAmazonIpReputationList'),
        managed(11, 'AWSManagedRulesKnownBadInputsRuleSet'),
        managed(12, 'AWSManagedRulesCommonRuleSet', ['GenericRFI_BODY', 'EC2MetaDataSSRF_BODY']),
      ],
    });

    // MinorunWeb（us-west-2）はリージョンをまたいで参照できないので、ARN を SSM に置き、
    // infra/scripts/domain.py が infra/.edge.json へ写す
    new ssm.StringParameter(this, 'WebAclArnParam', { parameterName: WEB_ACL_ARN_PARAM, stringValue: acl.attrArn });
    new cdk.CfnOutput(this, 'WebAclArn', { value: acl.attrArn });
  }
}
