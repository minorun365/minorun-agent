"""Claude API のクレジット（Max プランに付く月 $100）の見張り。

スライド作成機能は Claude API を先に使い、今月の使用額が線（CREDIT_LIMIT_USD）に近づいたら Bedrock へ回す（2026-11-21 みのるん指示）。
クレジットの残高を直接読む API は無いので、自分たちで使った額を足し上げる。
- 回答（と仕分け）が Claude API で払った分：会話の表の usage.calls のうち、モデル名が claude- で始まる呼び出しの usd
- スライド作成機能が Claude API で払った分：回数の表の credit#slides#<期> に足していく
足し上げた額は回数の表に5分だけ置き、1本ごとに会話の表を読み直さない。
クレジット切れのエラーが返ったら credit#off#<期> を立て、その期の残りは Bedrock だけで作る。
"""

import datetime
import json
import os
import time

import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")
QUOTA_TABLE = os.environ.get("SLIDES_TABLE", "")
LOG_TABLE = os.environ.get("CONVERSATION_TABLE", "")
KEY_ARN = os.environ.get("CLAUDE_API_KEY_ARN", "")
LIMIT = float(os.environ.get("CREDIT_LIMIT_USD", "90"))
JOB_ESTIMATE = float(os.environ.get("SLIDES_JOB_ESTIMATE_USD", "3"))
# クレジットが戻る日（毎月この日の日本時間0時から次の期）。Claude Console で確かめた日に合わせる
RESET_DAY = max(1, min(28, int(os.environ.get("CREDIT_RESET_DAY", "1"))))
CACHE_SECONDS = 300
JST = datetime.timezone(datetime.timedelta(hours=9))

# Opus 5.5 の Claude API の単価（100万トークンあたりのドル）。キャッシュの書き込みは入力の1.25倍、読み出しは $0.20
PRICE = {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0}

ddb = boto3.client("dynamodb", region_name=REGION)


def period_start(now=None):
    """今の期の始まり（日本時間）。RESET_DAY より前の日なら先月の RESET_DAY から"""
    now = (now or datetime.datetime.now(JST)).astimezone(JST)
    start = now.replace(day=RESET_DAY, hour=0, minute=0, second=0, microsecond=0)
    if now < start:  # 先月のその日（月の1日から1日戻ると先月の末日）
        start = (start.replace(day=1) - datetime.timedelta(days=1)).replace(day=RESET_DAY)
    return start


def period_key(now=None):
    return period_start(now).strftime("%Y-%m-%d")


def usd_of(usage):
    """Harness の metadata の usage（inputTokens など）からドルを出す"""
    return (usage.get("inputTokens", 0) * PRICE["input"] + usage.get("outputTokens", 0) * PRICE["output"]
            + usage.get("cacheReadInputTokens", 0) * PRICE["cache_read"]
            + usage.get("cacheWriteInputTokens", 0) * PRICE["cache_write"]) / 1e6


def _answers_usd(since):
    """会話の表から、期の始まり以降に Claude API で払った回答の額を足す（表に日付の索引が無いので全件を読む）"""
    if not LOG_TABLE:
        return 0.0
    total, kwargs = 0.0, {
        "TableName": LOG_TABLE, "ProjectionExpression": "#u.#c",
        "FilterExpression": "#t >= :s",
        "ExpressionAttributeNames": {"#u": "usage", "#c": "calls", "#t": "created_at"},
        "ExpressionAttributeValues": {":s": {"S": since.isoformat(timespec="seconds")}}}
    while True:
        page = ddb.scan(**kwargs)
        for item in page["Items"]:
            for call in item.get("usage", {}).get("M", {}).get("calls", {}).get("L", []):
                c = call.get("M", {})
                model, usd = c.get("model", {}).get("S", ""), c.get("usd", {}).get("N")
                if model.startswith("claude-") and usd:
                    total += float(usd)
        if "LastEvaluatedKey" not in page:
            return total
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _get(pk):
    if not QUOTA_TABLE:
        return {}
    return ddb.get_item(TableName=QUOTA_TABLE, Key={"pk": {"S": pk}}, ConsistentRead=True).get("Item") or {}


def _expires(days=70):
    return {"N": str(int(time.time()) + days * 86400)}


def spent(now=None):
    """今の期に Claude API で払った額（回答＋スライド）。回答の分は5分だけ控えを使う"""
    key = period_key(now)
    slides = float(_get(f"credit#slides#{key}").get("usd", {}).get("N", "0"))
    cache = _get(f"credit#answers#{key}")
    if cache and time.time() - float(cache.get("at", {}).get("N", "0")) < CACHE_SECONDS:
        answers = float(cache.get("usd", {}).get("N", "0"))
    else:
        answers = _answers_usd(period_start(now))
        if QUOTA_TABLE:
            ddb.put_item(TableName=QUOTA_TABLE, Item={"pk": {"S": f"credit#answers#{key}"}, "usd": {"N": f"{answers:.6f}"},
                                                      "at": {"N": str(time.time())}, "expires": _expires()})
    return answers + slides


def add_slides(usd, now=None):
    """スライド作成機能が Claude API で払った額を台帳に足す"""
    if QUOTA_TABLE and usd > 0:
        ddb.update_item(TableName=QUOTA_TABLE, Key={"pk": {"S": f"credit#slides#{period_key(now)}"}},
                        UpdateExpression="ADD usd :u SET expires = :e",
                        ExpressionAttributeValues={":u": {"N": f"{usd:.6f}"}, ":e": _expires()})


def switch_off(reason, now=None):
    """クレジット切れ・キーの失効など待っても直らない失敗。その期の残りは Bedrock だけにする"""
    if QUOTA_TABLE:
        ddb.put_item(TableName=QUOTA_TABLE, Item={"pk": {"S": f"credit#off#{period_key(now)}"}, "reason": {"S": reason[:200]},
                                                  "at": {"N": str(time.time())}, "expires": _expires()})


_KEY = {}


def key_ready():
    """Claude API のキー（AgentCore Identity の API キー資格情報）が作ってあるか。入れ物の中で1回だけ確かめる"""
    if "ok" not in _KEY:
        try:
            name = KEY_ARN.rsplit("/", 1)[-1]
            boto3.client("bedrock-agentcore-control", region_name=REGION).get_api_key_credential_provider(name=name)
            _KEY["ok"] = True
        except Exception as e:
            print(json.dumps({"credit_key": "missing", "reason": type(e).__name__}))
            _KEY["ok"] = False
    return _KEY["ok"]


def route(now=None):
    """この1本を Claude API（"api"）で作るか、Bedrock（"bedrock"）で作るか。理由も返す"""
    if not KEY_ARN or not key_ready():
        return "bedrock", "no_api_key"
    try:
        if _get(f"credit#off#{period_key(now)}"):
            return "bedrock", "switched_off"
        used = spent(now)
    except Exception as e:  # 台帳が読めないときは安全側（AWS 払い）に倒す
        print(json.dumps({"credit_meter": "unavailable", "reason": type(e).__name__}))
        return "bedrock", "meter_unavailable"
    if used + JOB_ESTIMATE >= LIMIT:
        return "bedrock", f"near_limit:{used:.2f}"
    return "api", f"spent:{used:.2f}"
