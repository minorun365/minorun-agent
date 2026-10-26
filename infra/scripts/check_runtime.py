"""本番の実設定を検査する。cdk.json の runtimeActive と入口・監視・V2設定を照合。

AWS_PROFILE=sandbox uv run --with boto3 python infra/scripts/check_runtime.py
AWS MCP からは verify(call_boto3, 'V2') を実行できる。
"""
import asyncio
import json

async def verify(call, expected):
    assert expected in ('V1', 'V2')
    async def api(service, operation, **params):
        return await call(service_name=service, operation_name=operation, region_name='us-west-2', params=params)
    identity = await api('sts', 'GetCallerIdentity')
    listing = await api('bedrock-agentcore-control', 'ListAgentRuntimes')
    by_name = {r['agentRuntimeName']: r for r in listing['agentRuntimes']}
    runtimes = await asyncio.gather(*[api('bedrock-agentcore-control','GetAgentRuntime',agentRuntimeId=by_name[name]['agentRuntimeId']) for name in ('minorun_agent','minorun_agent_v2')])
    old, new = runtimes
    assert old['platformVersion'] == 'V1' and new['platformVersion'] == 'V2', '実際のplatformVersionが不一致'
    assert all(r['status'] == 'READY' for r in runtimes), 'Runtime未準備'
    assert all(r['agentRuntimeArn'].split(':')[4] == identity['Account'] for r in runtimes), 'AWSアカウント不一致'
    for key in ('MEMORY_ID','LOG_TABLE','KNOWLEDGE_BUCKET','TOOLS_GATEWAY_URL','MODEL_MAIN','MODEL_AUX'):
        assert old['environmentVariables'].get(key) and old['environmentVariables'][key] == new['environmentVariables'].get(key), key
    active = new if expected == 'V2' else old
    gateways = await api('bedrock-agentcore-control','ListGateways')
    entry = next(g for g in gateways['items'] if g['name'] == 'minorun-agent-entry')
    gateway_id = entry['gatewayId']
    gateway = await api('bedrock-agentcore-control','GetGateway',gatewayIdentifier=gateway_id)
    targets = await api('bedrock-agentcore-control','ListGatewayTargets',gatewayIdentifier=gateway_id)
    details = await asyncio.gather(*[api('bedrock-agentcore-control','GetGatewayTarget',gatewayIdentifier=gateway_id,targetId=t['targetId']) for t in targets['items']])
    target_by_name = {t['name']:t for t in details}
    assert target_by_name['minorun']['targetConfiguration']['http']['agentcoreRuntime']['arn'] == active['agentRuntimeArn'], '本番接続先が不一致'
    assert set(target_by_name) == {'minorun'}, '不要な検証用の入口が残っている'
    assert all(t['status'] == 'READY' for t in details)
    limits = await api('bedrock-agentcore-control', 'ListGatewayRateLimits', gatewayIdentifier=gateway_id)
    assert any(limit['status'] == 'ACTIVE' and limit['dimensionKeys'] == ['targetName'] and
               any(entry['dimensions'] == {'targetName': 'minorun'} and
                   {'rate': 60, 'period': 'minute'} in entry['requests'] for entry in limit['entries'])
               for limit in limits['rateLimits']), '本番全体の毎分60件の上限が欠落'
    assert any('REQUEST' in i['interceptionPoints'] for i in gateway['interceptorConfigurations']), '確認役が欠落'
    permission = await api('iam', 'SimulatePrincipalPolicy', PolicySourceArn=gateway['roleArn'],
                           ActionNames=['bedrock-agentcore:InvokeAgentRuntime'],
                           ResourceArns=[r['agentRuntimeArn'] for r in runtimes])
    decisions = {r['EvalResourceName']: r['EvalResourceDecision']
                 for action in permission['EvaluationResults'] for r in action.get('ResourceSpecificResults', [])}
    assert all(decisions.get(r['agentRuntimeArn']) == 'allowed' for r in runtimes), '入口からRuntimeを呼ぶIAM権限が不足'
    for r in runtimes:
        policy = await api('bedrock-agentcore-control','GetResourcePolicy',resourceArn=r['agentRuntimeArn'])
        statements = json.loads(policy['policy'])['Statement']
        assert any(s['Effect']=='Deny' and s['Condition'].get('ArnNotEquals',{}).get('aws:PrincipalArn') == gateway['roleArn'] for s in statements), '直接呼出しの拒否が欠落'
        group_name = '/aws/bedrock-agentcore/runtimes/'+r['agentRuntimeId']+'-DEFAULT'
        groups = await api('logs','DescribeLogGroups',logGroupNamePrefix=group_name)
        assert next(g for g in groups['logGroups'] if g['logGroupName']==group_name)['retentionInDays']==180, 'ログ保持期間が不一致'
        filters = await api('logs','DescribeMetricFilters',logGroupName=group_name)
        assert any(t['metricName']=='AgentErrors' for f in filters['metricFilters'] for t in f['metricTransformations']), 'エラー計測が欠落'
    function = await api('lambda','GetFunctionConfiguration',FunctionName='minorun-agent-usage-page')
    env = function['Environment']['Variables']
    assert env['RUNTIME_ARN']==active['agentRuntimeArn'], '監視先が不一致'
    assert set(env['RUNTIME_ARNS'].split(','))=={r['agentRuntimeArn'] for r in runtimes}, '移行日の費用集計が欠落'
    return {'active':expected,'runtimes':[{'id':r['agentRuntimeId'],'version':r['agentRuntimeVersion'],'platform':r['platformVersion'],'status':r['status']} for r in runtimes], 'gateway':gateway_id,'checks':'routing, shared storage, request interceptor, invoke policy, retention, errors, cost'}

# CLI
if __name__ == '__main__':
    import os
    from pathlib import Path
    import boto3
    session = boto3.Session()
    async def call(**kw):
        client = session.client(kw['service_name'], region_name=kw['region_name'])
        operation = next(name for name, api in client.meta.method_to_api_mapping.items() if api==kw['operation_name'])
        def run():
            if client.can_paginate(operation):
                return client.get_paginator(operation).paginate(**kw['params']).build_full_result()
            return getattr(client, operation)(**kw['params'])
        return await asyncio.to_thread(run)
    config=json.loads((Path(__file__).resolve().parents[2]/'cdk.json').read_text())
    print(json.dumps(asyncio.run(verify(call,config['context']['runtimeActive'])),ensure_ascii=False,indent=2))
