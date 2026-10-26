"""X検索の実リソースとGateway接続を検査する（秘密値は取得しない）。"""
import json
import os


async def verify(call):
    async def api(service, operation, **params):
        return await call(service_name=service, operation_name=operation, region_name='us-west-2', params=params)
    identity = await api('sts', 'GetCallerIdentity')
    assert identity['Account'] == os.environ['EXPECTED_AWS_ACCOUNT_ID']
    fn = await api('lambda', 'GetFunctionConfiguration', FunctionName='minorun-agent-x-search')
    env = fn['Environment']['Variables']
    assert fn['State'] == 'Active' and fn['LastUpdateStatus'] == 'Successful'
    assert env['X_DAILY_CENTS'] == '500'
    assert env['X_SECRET_ID'] == 'minorun-agent/x-search'
    assert env['X_BUDGET_TABLE'] == 'minorun-agent-x-search'
    secret = await api('secretsmanager', 'DescribeSecret', SecretId=env['X_SECRET_ID'])
    assert any('AWSCURRENT' in stages for stages in secret['VersionIdsToStages'].values())
    table = await api('dynamodb', 'DescribeTable', TableName=env['X_BUDGET_TABLE'])
    assert table['Table']['TableStatus'] == 'ACTIVE'
    gateways = await api('bedrock-agentcore-control', 'ListGateways')
    gateway = next(g for g in gateways['items'] if g['name'] == 'minorun-agent-tools')
    gid = gateway['gatewayId']
    targets = await api('bedrock-agentcore-control', 'ListGatewayTargets', gatewayIdentifier=gid)
    target = next(t for t in targets['items'] if t['name'] == 'x')
    detail = await api('bedrock-agentcore-control', 'GetGatewayTarget', gatewayIdentifier=gid, targetId=target['targetId'])
    assert detail['status'] == 'READY'
    config = detail['targetConfiguration']['mcp']['lambda']
    assert config['lambdaArn'] == fn['FunctionArn']
    assert {t['name'] for t in config['toolSchema']['inlinePayload']} == {'users', 'posts'}
    return {'status': 'ok', 'gateway': gid, 'daily_cents': int(env['X_DAILY_CENTS']), 'tools': ['users', 'posts']}


if __name__ == '__main__':
    import asyncio
    import boto3
    session = boto3.Session()
    async def call(**kw):
        client = session.client(kw['service_name'], region_name=kw['region_name'])
        operation = next(n for n, a in client.meta.method_to_api_mapping.items() if a == kw['operation_name'])
        def run():
            if client.can_paginate(operation):
                return client.get_paginator(operation).paginate(**kw['params']).build_full_result()
            return getattr(client, operation)(**kw['params'])
        return await asyncio.to_thread(run)
    print(json.dumps(asyncio.run(verify(call)), ensure_ascii=False))
