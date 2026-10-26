"""回答が保存される前の通信障害も、独立した日別の記録から表示する。"""
import datetime
import html
import os
import urllib.parse

import boto3

LABELS = {'session_busy': '会話の起動競合', 'http': 'HTTPエラー', 'network': '通信できない',
          'stream': '回答が途中で切れた', 'agent': '回答処理のエラー'}
STATES = {'retrying': '再試行中', 'recovered': '自動復旧', 'failed': '未復旧'}


def read_errors(day):
    if not os.environ.get('ERROR_TABLE'):
        return None
    table = boto3.client('dynamodb')
    args = {'TableName': os.environ['ERROR_TABLE'], 'KeyConditionExpression': '#day = :day',
            'ExpressionAttributeNames': {'#day': 'day'}, 'ExpressionAttributeValues': {':day': {'S': day}}, 'ConsistentRead': True}
    rows = []
    try:
        while True:
            response = table.query(**args)
            rows.extend({k: (int(v['N']) if 'N' in v else v.get('S', '')) for k, v in item.items()} for item in response['Items'])
            if not response.get('LastEvaluatedKey'):
                break
            args['ExclusiveStartKey'] = response['LastEvaluatedKey']
    except Exception:
        return None
    return sorted(rows, key=lambda r: r['at'], reverse=True)


def error_card(rows, day):
    url = '?'+urllib.parse.urlencode({'v': 'errors', **({'d': day} if day else {})})
    count = '—' if rows is None else str(len(rows))
    unresolved = None if rows is None else sum(r['state'] != 'recovered' for r in rows)
    note = '取得できませんでした' if rows is None else (f'未復旧・再試行中 {unresolved}件' if unresolved else '詳細を見る ›')
    return f'<a class="card error-card" href="{html.escape(url)}"><span>この日の通信・回答エラー（全利用）</span><strong class="{"warn" if unresolved else ""}">{count}<small> 件</small></strong><span>{note}</span></a>'


def error_details(rows):
    if rows is None:
        content = '<p>エラー記録を取得できませんでした。次の更新で取り直します。</p>'
    elif not rows:
        content = '<p>この日の記録はありません。</p>'
    else:
        entries = []
        esc = lambda v: html.escape(str(v))
        for r in rows:
            at = datetime.datetime.fromisoformat(r['at'])
            date = f'{at.month}/{at.day}（{"月火水木金土日"[at.weekday()]}）{at:%H:%M:%S}'
            status = STATES.get(r['state'], '不明')
            test_label = ' · 動作テスト' if r['session_id'].startswith('e2e-') else ''
            updated = datetime.datetime.fromisoformat(r['updated_at'])
            updated_text = f'{updated.month}/{updated.day}（{"月火水木金土日"[updated.weekday()]}）{updated:%H:%M:%S}'
            user_url = '?'+urllib.parse.urlencode({'v': 'list', 'd': r['day'], 'u': r['actor']})
            entries.append(f'<details data-row="error-{esc(r["id"])}"><summary><div class="qbody"><div class="q">{esc(LABELS.get(r["kind"], "通信エラー"))} · <span class="{"warn" if r["state"] != "recovered" else ""}">{status}</span></div><div class="meta">{date}{test_label} · {"先読み" if r["phase"] == "warmup" else "質問・回答"}</div></div></summary><div class="ans"><p>HTTP：{esc(r["status"])} ／ 試行：{esc(r["attempts"])}回</p><p>利用者：<a href="{html.escape(user_url)}">{esc(r["actor"])}</a></p><p>会話：{esc(r["session_id"])}</p><p>質問ID：{esc(r.get("tid") or "なし")}</p><p>最終記録：{updated_text}</p></div></details>')
        content = ''.join(entries)
    return '<div class="card"><h2>通信・回答エラー</h2><p class="legend">ブラウザが観測した記録です。自動復旧も残します。通信が完全に切れて記録を送れない場合は含まれません。</p><div class="list">'+content+'</div></div>'
