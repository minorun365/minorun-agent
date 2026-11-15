"""スライドの依頼を受け付ける（ツール make_slides の中身）。

回数を表で数え（1人1日・全体で1日）、進み具合の最初の1行を公開用 S3 に置き、スライド工房（Lambda）を非同期で起動する。
数えるのはログインした人だけ。ログインしていない人には login_required を返す。
"""

import datetime
import ipaddress
import json
import secrets
import time
import urllib.parse

import boto3

from settings import JST, REGION

_clients = {}


def _client(name):
    if name not in _clients:
        _clients[name] = boto3.client(name, region_name=REGION)
    return _clients[name]


def valid_url(url):
    try:
        u = urllib.parse.urlparse(url.strip())
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        return False
    host = u.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


def _count(table, user, user_daily, total_daily):
    """1人と全体の件数を同時に1つ増やす。どちらかが上限なら増やさずに理由を返す。"""
    day = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    expires = int(time.time()) + 3 * 86400
    def item(pk, limit):
        return {"Update": {"TableName": table, "Key": {"pk": {"S": pk}},
                           "UpdateExpression": "ADD n :one SET expires = :exp",
                           "ConditionExpression": "attribute_not_exists(n) OR n < :lim",
                           "ExpressionAttributeValues": {":one": {"N": "1"}, ":lim": {"N": str(limit)},
                                                         ":exp": {"N": str(expires)}}}}
    try:
        _client("dynamodb").transact_write_items(TransactItems=[item(f"user#{user}#{day}", user_daily),
                                                                item(f"all#{day}", total_daily)])
        return None
    except _client("dynamodb").exceptions.TransactionCanceledException as e:
        reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
        if reasons and reasons[0] == "ConditionalCheckFailed":
            return "limit"
        if len(reasons) > 1 and reasons[1] == "ConditionalCheckFailed":
            return "busy"
        raise


def start(url, request, user, *, function, table, bucket, user_daily, total_daily, unlimited=False):
    """unlimited は運営者のテスト用。1人の上限にも全体の上限にも数えない。"""
    if not function:
        return {"status": "unavailable", "note": "この環境ではスライド作成を使えません"}
    if not user:
        return {"status": "login_required", "note": "スライド作成はログインした人だけが使えます。画面右上からGoogleでログインできます"}
    url = url.strip()
    if not valid_url(url):
        return {"status": "invalid", "note": "公開されているページの http(s) のURLが必要です"}
    blocked = None if unlimited else _count(table, user, user_daily, total_daily)
    if blocked == "limit":
        return {"status": "limit", "note": f"今日の分（1人1日{user_daily}本）を使い切りました。明日（日本時間の0時以降）にまた使えます"}
    if blocked == "busy":
        return {"status": "busy", "note": "今日は全体の上限に達しました。明日また使えます"}
    job = secrets.token_urlsafe(12).replace("-", "a").replace("_", "b")
    _client("s3").put_object(Bucket=bucket, Key=f"s/{job}/status.json", ContentType="application/json",
                             CacheControl="no-cache, max-age=0",
                             Body=json.dumps({"stage": "queued", "at": int(time.time())}).encode())
    _client("lambda").invoke(FunctionName=function, InvocationType="Event",
                             Payload=json.dumps({"job": job, "url": url, "ask": str(request)[:300], "user": user}).encode())
    _remember(table, user, job)
    print(json.dumps({"slides_job": job, "user": user, **({"unlimited": True} if unlimited else {})}))
    return {"status": "accepted", "job": job, "note": "数分ででき上がります。画面を離れても、右上のアイコンの「作ったスライド」から開けます"}


KEEP = 10  # 一覧に出す件数（新しい順）


def _remember(table, user, job):
    """利用者ごとの「作ったスライド」に番号と時刻だけを足す（記事のURLは残さない）。画面を閉じても後から開けるように。"""
    try:
        _client("dynamodb").update_item(
            TableName=table, Key={"pk": {"S": f"list#{user}"}},
            UpdateExpression="SET jobs = list_append(if_not_exists(jobs, :empty), :new)",
            ExpressionAttributeValues={":empty": {"L": []},
                                       ":new": {"L": [{"M": {"job": {"S": job}, "at": {"N": str(int(time.time()))}}}]}})
    except Exception as e:  # 一覧に載らないだけで、スライド作りは続ける
        print(json.dumps({"slides_list_error": type(e).__name__}))


def recent(table, user):
    """その人が作ったスライドを新しい順に最大 KEEP 件返す。"""
    if not (table and user):
        return []
    item = _client("dynamodb").get_item(TableName=table, Key={"pk": {"S": f"list#{user}"}}).get("Item") or {}
    jobs = [{"job": j["M"]["job"]["S"], "at": int(j["M"]["at"]["N"])} for j in item.get("jobs", {}).get("L", [])]
    return sorted(jobs, key=lambda j: -j["at"])[:KEEP]
