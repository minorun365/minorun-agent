"""回答側の Claude API クレジットの線（スライド作成機能の slides/credit.py と同じ台帳を読む）。

Max プランの API クレジット（月 $100）は、回答とスライド作成機能が同じ組織で使う。今の期に使った額
（回答＋スライド）が線（CREDIT_LIMIT_USD）を超えたら、回答も Bedrock だけで答える（2026-11-22）。
台帳は回数の表（SLIDES_TABLE）の次の行で、書き方はスライド作成機能と揃える。
- credit#off#<期>：クレジット切れなどで Claude API を止めた印。立っていればその期の残りは Bedrock
- credit#slides#<期>：スライド作成機能が Claude API で払った額
- credit#answers#<期>：回答が Claude API で払った額の控え（会話の表を足し上げた結果を5分だけ置く）
台帳が読めないときは Claude API を止めない（自動チャージは切ってあり、本当に尽きれば 402 で Bedrock へ回る）。
"""

import datetime
import json
import os
import time

REGION = os.environ.get("AWS_REGION", "us-west-2")
QUOTA_TABLE = os.environ.get("SLIDES_TABLE", "")
LOG_TABLE = os.environ.get("LOG_TABLE", "")
LIMIT = float(os.environ.get("CREDIT_LIMIT_USD", "90"))
# クレジットが戻る日（毎月この日の日本時間0時から次の期）。スライド作成機能と同じ値を渡す
RESET_DAY = max(1, min(28, int(os.environ.get("CREDIT_RESET_DAY", "1"))))
CACHE_SECONDS = 300
JST = datetime.timezone(datetime.timedelta(hours=9))

_ddb = {}


def _client():
    if "c" not in _ddb:
        import boto3
        _ddb["c"] = boto3.client("dynamodb", region_name=REGION)
    return _ddb["c"]


def period_start(now=None):
    """今の期の始まり（日本時間）。RESET_DAY より前の日なら先月の RESET_DAY から"""
    now = (now or datetime.datetime.now(JST)).astimezone(JST)
    start = now.replace(day=RESET_DAY, hour=0, minute=0, second=0, microsecond=0)
    if now < start:
        start = (start.replace(day=1) - datetime.timedelta(days=1)).replace(day=RESET_DAY)
    return start


def period_key(now=None):
    return period_start(now).strftime("%Y-%m-%d")


def _get(pk):
    return _client().get_item(TableName=QUOTA_TABLE, Key={"pk": {"S": pk}}, ConsistentRead=True).get("Item") or {}


def _expires(days=70):
    return {"N": str(int(time.time()) + days * 86400)}


def _answers_usd(since):
    """会話の表から、期の始まり以降に Claude API で払った回答の額を足す（slides/credit.py と同じ数え方）"""
    if not LOG_TABLE:
        return 0.0
    total, kwargs = 0.0, {
        "TableName": LOG_TABLE, "ProjectionExpression": "#u.#c",
        "FilterExpression": "#t >= :s",
        "ExpressionAttributeNames": {"#u": "usage", "#c": "calls", "#t": "created_at"},
        "ExpressionAttributeValues": {":s": {"S": since.isoformat(timespec="seconds")}}}
    while True:
        page = _client().scan(**kwargs)
        for item in page["Items"]:
            for call in item.get("usage", {}).get("M", {}).get("calls", {}).get("L", []):
                c = call.get("M", {})
                model, usd = c.get("model", {}).get("S", ""), c.get("usd", {}).get("N")
                if model.startswith("claude-") and usd:
                    total += float(usd)
        if "LastEvaluatedKey" not in page:
            return total
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def spent(now=None):
    """今の期に Claude API で払った額（回答＋スライド）。回答の分は5分だけ控えを使う"""
    key = period_key(now)
    slides = float(_get(f"credit#slides#{key}").get("usd", {}).get("N", "0"))
    cache = _get(f"credit#answers#{key}")
    if cache and time.time() - float(cache.get("at", {}).get("N", "0")) < CACHE_SECONDS:
        answers = float(cache.get("usd", {}).get("N", "0"))
    else:
        answers = _answers_usd(period_start(now))
        _client().put_item(TableName=QUOTA_TABLE, Item={
            "pk": {"S": f"credit#answers#{key}"}, "usd": {"N": f"{answers:.6f}"},
            "at": {"N": str(time.time())}, "expires": _expires()})
    return answers + slides


class _Gate:
    checked_at = -1e9
    blocked = None


def blocked(now=None):
    """Claude API を使わない理由（線を超えた・止めた印がある）。使ってよければ None。入れ物の中で5分に1回だけ台帳を読む"""
    if not QUOTA_TABLE:
        return None
    if time.monotonic() - _Gate.checked_at < CACHE_SECONDS:
        return _Gate.blocked
    _Gate.checked_at = time.monotonic()
    try:
        if _get(f"credit#off#{period_key(now)}"):
            _Gate.blocked = "credit_switched_off"
        else:
            used = spent(now)
            _Gate.blocked = f"credit_near_limit:{used:.2f}" if used >= LIMIT else None
    except Exception as e:  # 台帳が読めないときは止めない（尽きれば 402 で Bedrock へ回る）
        print(json.dumps({"credit_meter": "unavailable", "reason": type(e).__name__}), flush=True)
        _Gate.blocked = None
    if _Gate.blocked:
        print(json.dumps({"model_route": "credit_gate", "reason": _Gate.blocked}), flush=True)
    return _Gate.blocked


def switch_off(reason, now=None):
    """クレジット切れ（402・残高不足）を受けたら、その期の残りはスライド作成機能も Bedrock だけにする"""
    if not QUOTA_TABLE:
        return
    try:
        _client().put_item(TableName=QUOTA_TABLE, Item={
            "pk": {"S": f"credit#off#{period_key(now)}"}, "reason": {"S": reason[:200]},
            "at": {"N": str(time.time())}, "expires": _expires()})
    except Exception as e:
        print(json.dumps({"credit_ledger": "switch_off_failed", "reason": type(e).__name__}), flush=True)
