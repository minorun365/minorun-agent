"""利用量と概算コストを、スマホで数秒で読めるページにする（日本時間で1日を区切る）。

画面はログインした管理者（ADMIN_EMAIL）だけが見られる（Cognito のマネージドログイン。/login → /callback で
署名つきの cookie を置く）。JSON は外部のダッシュボードがブラウザから読むので、これまでどおり合言葉 ?k= で返す。

  GET <関数URL>                          … 要約（今日の質問数・利用者数・評価・概算コスト）
  GET <関数URL>?v=hours&d=YYYY-MM-DD      … 1時間ごとの表（会話の数・質問の数・概算コスト）
  GET <関数URL>?v=list&d=YYYY-MM-DD&f=bad&p=2 … 質問の一覧（新しい順・50件ずつ。f で評価あり・良くない・自由入力に絞る）
  GET <関数URL>?k=<合言葉>&format=json&d=YYYY-MM-DD … その日の要約・1時間ごと・質問の一覧・要注意の一覧（JSON）
  （要約には、公開からの累計・直近7日の1日平均×30日の月額ペースも出す）
  EventBridge から15分おき … 今日の概算コストを MinorunAgent/EstimatedCostUsd に出す（アラームが見る）

正本は1問1行の表 minorun-agent-conversations。数字は概算で、Bedrock のトークン数はアカウント全体
（同じアカウントの他のアプリも同じモデルを使うと混ざる）。通知は当日1万円超・直近30日3万円超（1ドル150円の参考換算）。
"""

import base64
import datetime
import hashlib
import hmac
import html
import json
import math
import os
import re
import secrets
import statistics
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid

import boto3
import periods
from transport_errors import read_errors, error_card, error_details

JST = datetime.timezone(datetime.timedelta(hours=9))
TOKEN = os.environ["PAGE_TOKEN"]
TABLE = os.environ["LOG_TABLE"]
RUNTIME_ARN = os.environ["RUNTIME_ARN"]
RUNTIME_ARNS = list(dict.fromkeys(a.strip() for a in os.environ.get("RUNTIME_ARNS", RUNTIME_ARN).split(",") if a.strip()))
TOOLS_GATEWAY_ARN = os.environ["TOOLS_GATEWAY_ARN"]
YEN = float(os.environ.get("YEN_PER_USD", "150"))
ALERT_USD = float(os.environ.get("ALERT_USD", str(10000 / 150)))
PAGE_SIZE = 50
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").lower()
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
AUTH_DOMAIN = os.environ.get("AUTH_DOMAIN", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
APP_USER_POOL_ID = os.environ.get("APP_USER_POOL_ID", "")
_registration_cache = None
# 専用の疎通テストID。通常の回数制限も費用計測も通す。
TEST_ACTORS = {hashlib.sha256(str(uuid.uuid5(uuid.NAMESPACE_URL, "minorun-agent:test:" + mode)).encode()).hexdigest()[:16]
               for mode in ("stream", "burst")}
AUDIENCE_TABLE = os.environ.get("AUDIENCE_TABLE", "minorun-agent-usage")
AUDIENCES = {"general": "一般利用", "owner": "本人", "test": "動作テスト", "all": "すべて"}
# 公開URL（https://agent.minoruonda.com/admin/）。CloudFront の /admin* がこの関数へ中継する
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://agent.minoruonda.com/admin/")
COOKIE_PATH = urllib.parse.urlparse(PUBLIC_BASE).path.rstrip("/") or "/"
SESSION_DAYS = 7
# 累計コストの起点（公開前の検証を含む、会話の表の最初の行がある日の日本時間0時）
COST_SINCE = datetime.datetime.fromisoformat(os.environ.get("COST_SINCE", "2026-10-25T00:00:00+09:00"))
_SESSION_KEY = hashlib.sha256(f"usage-page-session:{TOKEN}".encode()).digest()

# 単価（ドル）。AWS の料金 API（us-west-2・global の推論プロファイル）で 2026-10-25 に確かめた値。Brave は 2026-10-24 の検証の値
PRICE = {
    "sonnet": {"in": 2.00, "out": 10.00, "cache_read": 0.20, "cache_write": 2.50},   # 100万トークンあたり
    "haiku": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "vcpu_hour": 0.0895,
    "gb_hour": 0.00945,
    "web_search": 0.012,      # 1件（$12 / 1,000件）
    "brave": 0.005,           # 1件（$5 / 1,000件）
    "kb_retrieve": 0.001,     # 1件
}
MODELS = {"sonnet": "global.anthropic.claude-sonnet-5-5", "haiku": "global.anthropic.claude-haiku-5-5"}
TOKEN_METRICS = {"in": "InputTokenCount", "out": "OutputTokenCount",
                 "cache_read": "CacheReadInputTokenCount", "cache_write": "CacheWriteInputTokenCount"}
LABELS = {"sonnet": "Sonnet 5.5（回答）", "haiku": "Haiku 5.5（判定・要約・候補）", "runtime": "AgentCore Runtime",
          "web_search": "AgentCoreWebSearch", "brave": "Brave", "x": "X検索", "kb": "ナレッジベースの検索"}
REASONS = {"per_minute": "1分の上限", "daily_limit": "1日の上限", "bad_id": "IDの形", "too_long": "文字数", "stopped": "停止中", "banned": "利用停止"}
FLAG_LABELS = {"off_topic": "目的外", "injection": "指示の書き換え", "personal_info": "個人情報", "bug_report": "不具合の報告", "abuse": "良識の逸脱"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(\+?\d[\d\- ]{8,}\d)")

cw = boto3.client("cloudwatch")
ddb = boto3.client("dynamodb")


def _day(d):
    """d（YYYY-MM-DD か None）→ その日の日本時間0時と、終わり（今日なら今）"""
    now = datetime.datetime.now(JST)
    _, start, end = periods.bounds(d, now, COST_SINCE)
    return now, start, end


def _metrics(start, end, period):
    """コストに効くメトリクスを、period 秒ごとの {時刻: 値} で返す"""
    queries, keys = [], []

    def add(key, ns, name, dims):
        queries.append({"Id": f"m{len(queries)}", "ReturnData": True, "MetricStat": {
            "Metric": {"Namespace": ns, "MetricName": name, "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]},
            "Period": period, "Stat": "Sum"}})
        keys.append(key)

    for model, model_id in MODELS.items():
        for kind, name in TOKEN_METRICS.items():
            add((model, kind), "AWS/Bedrock", name, {"ModelId": model_id})
    for arn in RUNTIME_ARNS:
        rt = {"Resource": arn, "Service": "AgentCore.Runtime"}
        add("vcpu", "AWS/Bedrock-AgentCore", "CPUUsed-vCPUHours", rt)
        add("gb", "AWS/Bedrock-AgentCore", "MemoryUsed-GBHours", rt)
    add("web", "AWS/Bedrock-AgentCore", "Invocations", {
        "Resource": TOOLS_GATEWAY_ARN, "Operation": "InvokeGateway", "Method": "tools/call",
        "Protocol": "MCP", "Name": "web___WebSearch"})
    out = {k: {} for k in keys}
    kwargs = dict(MetricDataQueries=queries, StartTime=start, EndTime=end)
    while True:
        res = cw.get_metric_data(**kwargs)
        for r in res["MetricDataResults"]:
            series = out[keys[int(r["Id"][1:])]]
            for t, v in zip(r["Timestamps"], r["Values"]):
                hour = t.astimezone(JST).replace(minute=0, second=0, microsecond=0)
                series[hour] = series.get(hour, 0.0) + float(v)
        if not res.get("NextToken"):
            break
        kwargs["NextToken"] = res["NextToken"]
    return out


def _rejected(start, end):
    kwargs = dict(MetricDataQueries=[{
        "Id": "r", "Expression": "SEARCH('{MinorunAgent,reason} MetricName=\"Rejected\"', 'Sum', 3600)", "ReturnData": True}],
        StartTime=start, EndTime=end)
    counts = {}
    while True:
        res = cw.get_metric_data(**kwargs)
        for r in res["MetricDataResults"]:
            if r["Values"]:
                counts[r["Label"]] = counts.get(r["Label"], 0) + int(sum(r["Values"]))
        if not res.get("NextToken"):
            return counts
        kwargs["NextToken"] = res["NextToken"]


def _rows(start, end, fields):
    """その日の行。created_at は日本時間の ISO 文字列なので、文字列の大小で範囲を切れる"""
    rows, kwargs = [], {
        "TableName": TABLE, "ProjectionExpression": ", ".join(f"#f{i}" for i in range(len(fields))),
        "ExpressionAttributeNames": {f"#f{i}": f for i, f in enumerate(fields)},
        "FilterExpression": "#c >= :s AND #c < :e",
        "ExpressionAttributeValues": {":s": {"S": start.isoformat(timespec="seconds")},
                                      ":e": {"S": end.isoformat(timespec="seconds")}}}
    kwargs["ExpressionAttributeNames"]["#c"] = "created_at"
    while True:
        page = ddb.scan(**kwargs)
        rows += [_plain(r) for r in page["Items"]]
        if "LastEvaluatedKey" not in page:
            return rows
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _plain(item):
    def value(v):
        t, val = next(iter(v.items()))
        if t == "M":
            return {k: value(x) for k, x in val.items()}
        if t == "L":
            return [value(x) for x in val]
        if t == "N":
            return float(val)
        return None if t == "NULL" else val
    return {k: value(v) for k, v in item.items()}


def _model_usd(row):
    """完了したモデル使用量を優先し、記録のない事前回答だけ0円で補う。"""
    usage = row.get("usage")
    if usage is None and row.get("prebaked") in ("first", "follow"):
        return 0.0
    if not isinstance(usage, dict) or usage.get("complete") is not True:
        return None
    usd = usage.get("usd")
    if type(usd) not in (int, float) or not math.isfinite(usd) or usd < 0:
        return None
    return usd


def _model_cost_label(row):
    usd = _model_usd(row)
    if usd is None:
        return "モデル費用 未計測"
    yen = usd * YEN
    if yen == 0:
        return "モデル費用0円"
    if yen < 0.01:
        return "モデル費用0.01円未満"
    return f"モデル費用 約{yen:,.2f}円"


def _tool_costs(rows):
    counts = {}
    for r in rows:
        for t in r.get("tools") or []:
            counts[t] = counts.get(t, 0) + 1
    return counts, (counts.get("search_recent", 0) + counts.get("search_japanese", 0)) * PRICE["brave"], \
        (counts.get("search_2026", 0) + counts.get("search_past", 0)) * PRICE["kb_retrieve"]


def _x_cost(rows):
    """取得件数からの概算。失敗時は事前予約額が記録されていれば安全側に含める。"""
    return sum(float(item.get("estimated_usd") if item.get("estimated_usd") is not None else item.get("reserved_usd") or 0)
               for row in rows for item in row.get("x_search", []))


def _cost(m, when, brave, kb, since=None, x=0):
    """when の時台（None なら全部。since があればその時台以降）の概算コストの内訳"""
    def s(key):
        series = m[key]
        if when is not None:
            return series.get(when, 0.0)
        return sum(v for h, v in series.items() if since is None or h >= since)
    cost = {model: sum(s((model, k)) / 1e6 * PRICE[model][k] for k in TOKEN_METRICS) for model in MODELS}
    cost["runtime"] = s("vcpu") * PRICE["vcpu_hour"] + s("gb") * PRICE["gb_hour"]
    cost["web_search"] = s("web") * PRICE["web_search"]
    cost["brave"] = brave
    cost["x"] = x
    cost["kb"] = kb
    return cost


_pace_cache = {}


def _pace(now):
    """公開からの累計・直近7日の概算コストと、このペースでの月額（直近7日の1日平均×30日）。
    公開から7日たつまでは、公開からの日数で割って1日平均を出す。15秒ごとの部分更新で毎回引かないよう60秒覚える。"""
    cached = _pace_cache.get("v")
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    since = COST_SINCE
    week = max(since, (now - datetime.timedelta(days=7)).replace(minute=0, second=0, microsecond=0))
    m = _metrics(since, now, 3600)
    rows = _rows(since, now, ["created_at", "tools", "x_search"])
    recent = [r for r in rows if datetime.datetime.fromisoformat(r["created_at"]) >= week]
    _, brave, kb = _tool_costs(rows)
    _, wbrave, wkb = _tool_costs(recent)
    total = sum(_cost(m, None, brave, kb, x=_x_cost(rows)).values())
    week_usd = sum(_cost(m, None, wbrave, wkb, since=week, x=_x_cost(recent)).values())
    days = max((now - week).total_seconds() / 86400, 1)
    out = {"since": since.isoformat(timespec="minutes"), "total_usd": total, "week_usd": week_usd,
           "week_days": min(days, 7), "day_usd": week_usd / days, "month_usd": week_usd / days * 30}
    _pace_cache["v"] = (time.time(), out)
    return out


def summary(day=None):
    now, start, end = _day(day)
    m = _metrics(start, end, 86400)
    rows = _rows(start, end, ["created_at", "anon", "tools", "first_ms", "feedback", "via", "x_search"])
    tools, brave, kb = _tool_costs(rows)
    cost = _cost(m, None, brave, kb, x=_x_cost(rows))
    firsts = sorted(r["first_ms"] for r in rows if r.get("first_ms") is not None)
    return {"now": now, "day": start, "questions": len(rows), "users": len({r.get("anon") for r in rows}),
            "first_p50": statistics.median(firsts) if firsts else None, "rejected": _rejected(start, end),
            "tools": tools, "cost": cost, "total": sum(cost.values()),
            "feedback": {v: sum(1 for r in rows if r.get("feedback") == v) for v in ("good", "bad")},
            "via": {v: sum(1 for r in rows if r.get("via") == v) for v in VIA_LABELS},
            "pace": _pace(now)}


def hours(day):
    now, start, end = _day(day)
    m = _metrics(start, end, 3600)
    rows = _rows(start, end, ["created_at", "pk", "tools", "x_search"])
    by_hour = {}
    for r in rows:
        h = datetime.datetime.fromisoformat(r["created_at"]).replace(minute=0, second=0, microsecond=0)
        by_hour.setdefault(h, []).append(r)
    out, h = [], start
    while h < end:
        rs = by_hour.get(h, [])
        _, brave, kb = _tool_costs(rs)
        out.append({"hour": h, "sessions": len({r.get("pk") for r in rs}), "questions": len(rs),
                    "usd": sum(_cost(m, h, brave, kb, x=_x_cost(rs)).values())})
        h += datetime.timedelta(hours=1)
    total = {"sessions": len({r.get("pk") for r in rows}), "questions": len(rows), "usd": sum(x["usd"] for x in out)}
    return now, start, list(reversed(out)), total


def questions(day):
    now, start, end = _day(day)
    rows = _rows(start, end, ["created_at", "question", "answer", "topic", "tools", "first_ms", "flag", "flag_reason", "anon", "user", "pk", "feedback", "via", "prebaked", "usage", "x_search"])
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return now, start, rows


def _registered_users():
    """Google 登録者。管理者用とは別のプールを全ページ読み、ハッシュだけを保持する。"""
    global _registration_cache
    if not APP_USER_POOL_ID:
        return None
    if _registration_cache and _registration_cache[0] > time.monotonic():
        return _registration_cache[1]
    client = boto3.client("cognito-idp")
    kwargs = {"UserPoolId": APP_USER_POOL_ID, "AttributesToGet": ["sub"], "Limit": 60}
    users = {}
    try:
        while True:
            res = client.list_users(**kwargs)
            for user in res["Users"]:
                username = user["Username"]
                if username.lower().startswith("google_"):
                    key = "user-" + hashlib.sha256(f"user:{username}".encode()).hexdigest()[:16]
                    users[key] = {"registered_at": user["UserCreateDate"].astimezone(JST).isoformat()} if user.get("UserCreateDate") else {}
            if not res.get("PaginationToken"):
                break
            kwargs["PaginationToken"] = res["PaginationToken"]
    except Exception:
        # 取得失敗を登録0人と表示しない。名前や認証情報はログへ出さない。
        print(json.dumps({"registration": "unavailable"}))
        return None
    _registration_cache = (time.monotonic() + 60, users)
    return users


def _actor(r):
    return f"user-{r['user']}" if r.get("user") else r.get("anon") or "unknown"


def _identifier(actor):
    # 既存の乱数／Google 固有番号由来の16桁をそのまま使い、日をまたいでも変えない。
    if actor == "unknown":
        return "不明"
    return "G-" + actor[5:] if actor.startswith("user-") else "A-" + actor


def bans():
    """利用停止の一覧（回数の表の pk=ban#a#… / ban#u#…）。新しい順"""
    items, kwargs = [], {"TableName": AUDIENCE_TABLE, "ConsistentRead": True, "FilterExpression": "begins_with(pk, :p)",
                         "ExpressionAttributeValues": {":p": {"S": "ban#"}}}
    while True:
        page = ddb.scan(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    out = []
    for i in items:
        kind, value = i["pk"]["S"].split("#")[1:]
        at = i.get("at", {}).get("S", "")
        out.append({"identifier": ("A-" if kind == "a" else "G-") + value, "at": at, "source": i.get("source", {}).get("S", ""),
                    "reason": i.get("reason", {}).get("S", ""), "question": i.get("question", {}).get("S", ""),
                    "day": i.get("day", {}).get("S", "") or at[:10], "linked_from": i.get("linked_from", {}).get("S", ""),
                    "views": int(i.get("views", {}).get("N", "0")), "attempts": int(i.get("attempts", {}).get("N", "0")),
                    "seen_at": i.get("seen_at", {}).get("S", ""),
                    **{k: i.get(k, {}).get("S", "") for k in ("conv_pk", "conv_sk", "reviewed_at")}})
    return sorted(out, key=lambda b: b["at"], reverse=True)


def _ban_summary(items, now):
    """要約カード用：停止中のID数と、詳細をまだ確認していない停止。"""
    recent = [b for b in items if not b.get("reviewed_at")]
    seen = [b["seen_at"] for b in items if b.get("seen_at")]
    return {"total": len(items), "new": len(recent), "ids": {b["identifier"] for b in items},
            "day": items[0]["day"] if items else None,
            "views": sum(b.get("views", 0) for b in items), "attempts": sum(b.get("attempts", 0) for b in items),
            "seen_at": max(seen) if seen else None}


def _ban_key(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[AG]-[0-9a-f]{16}", identifier):
        raise ValueError("invalid identifier")
    return f"ban#{'a' if identifier[0] == 'A' else 'u'}#{identifier[2:]}"


def _ban_history(identifier):
    """停止したIDに一致する書き込みを、日付を限定せず全ページ読む。"""
    _ban_key(identifier)
    value = identifier[2:]
    fields = ["pk", "sk", "created_at", "question", "answer", "anon", "user", "flag", "flag_reason", "via"]
    kwargs = {"TableName": TABLE,
              "ProjectionExpression": ",".join(f"#f{i}" for i in range(len(fields))),
              "ExpressionAttributeNames": {f"#f{i}": f for i, f in enumerate(fields)}}
    kwargs["ExpressionAttributeNames"].update({"#anon": "anon"})
    if identifier.startswith("G-"):
        kwargs["ExpressionAttributeNames"]["#user"] = "user"
        kwargs["FilterExpression"] = "#user = :id OR #anon = :legacy"
        kwargs["ExpressionAttributeValues"] = {":id": {"S": value}, ":legacy": {"S": "user-" + value}}
    else:
        kwargs["FilterExpression"] = "#anon = :id"
        kwargs["ExpressionAttributeValues"] = {":id": {"S": value}}
    rows = []
    while True:
        result = ddb.scan(**kwargs)
        rows.extend(_plain(r) for r in result["Items"])
        if not result.get("LastEvaluatedKey"):
            return sorted(rows, key=lambda r: (r.get("created_at", ""), r.get("sk", "")), reverse=True)
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]


def _review_ban(event):
    expected = urllib.parse.urlsplit(PUBLIC_BASE)
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("origin") != f"{expected.scheme}://{expected.netloc}":
        return _html("forbidden", 403)
    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        data = json.loads(raw)
        csrf = _verify(data.get("csrf", ""))
        if not csrf or csrf.get("purpose") != "ban-review" or not csrf.get("at"):
            return _html("invalid request", 400)
        pk = _ban_key(csrf.get("identifier"))
    except (ValueError, TypeError, AttributeError):
        return _html("invalid request", 400)
    try:
        ddb.update_item(TableName=AUDIENCE_TABLE, Key={"pk": {"S": pk}},
                        UpdateExpression="SET reviewed_at = if_not_exists(reviewed_at, :now)",
                        ConditionExpression="attribute_exists(pk) AND #at = :at",
                        ExpressionAttributeNames={"#at": "at"},
                        ExpressionAttributeValues={":now": {"S": datetime.datetime.now(JST).isoformat(timespec="seconds")},
                                                   ":at": {"S": csrf["at"]}})
    except ddb.exceptions.ConditionalCheckFailedException:
        # 詳細を開いた後に解除・再停止された別の記録を既読にしない。
        return _html("停止記録が更新されました。ページを読み込み直してください。", 409)
    return _html("ok")


def _ban_time(value):
    if not value:
        return "記録なし"
    try:
        t = datetime.datetime.fromisoformat(value).astimezone(JST)
        return f'{t:%Y/%-m/%-d}（{"月火水木金土日"[t.weekday()]}）{t:%H:%M:%S}'
    except ValueError:
        return "記録なし"


def _ban_cause(b, items):
    """linked は元の停止へたどる。文面や時刻の近さから原因を推定しない。"""
    by_key = {_ban_key(x["identifier"]): x for x in items}
    visited = set()
    while b.get("source") == "linked" and b.get("linked_from") in by_key:
        key = b["linked_from"]
        if key in visited:
            break
        visited.add(key)
        b = by_key[key]
    return b


def _caused_ban(row, cause):
    return bool(cause.get("conv_pk") and cause.get("conv_sk") and
                row.get("pk") == cause["conv_pk"] and row.get("sk") == cause["conv_sk"])


def _ban_post(row, cause, opened=False):
    decisive = _caused_ban(row, cause)
    label = '<span class="alert">停止の決め手になった書き込み</span> · ' if decisive else ""
    meta = label + html.escape(_ban_time(row.get("created_at")))
    if row.get("flag_reason"):
        meta += " · " + html.escape(_mask(row["flag_reason"]))
    key = html.escape(("cause|" if opened else "history|") + str(row.get("pk", "")) + "|" + str(row.get("sk", "")), quote=True)
    return (f'<details data-row="{key}"{" open" if opened else ""}>'
            f'<summary><div class="qbody"><div class="meta">{meta}</div>'
            f'<div class="q">{html.escape(_mask(row.get("question")))}</div></div></summary>'
            f'<div class="ans">{_md(_mask(row.get("answer")))}</div></details>')


def render_bans(identifier=None, page=1):
    items = bans()
    selected = next((b for b in items if b["identifier"] == identifier), None)
    now = datetime.datetime.now(JST)
    content, token = [], ""
    if identifier and not selected:
        content.append('<p>このIDの利用停止記録はありません。</p>')
    elif not selected:
        content.append('<p class="note">IDを選ぶと、停止の理由・書き込み・停止後のアクセスを確認できます。</p>')
        for b in items:
            new = '<span class="alert">新着</span>' if not b.get("reviewed_at") else '<span class="meta">確認済み</span>'
            content.append(f'<a class="ban-person" href="{html.escape(_url(v="bans", u=b["identifier"]))}">'
                           f'<div class="ban-person-title"><strong>{html.escape(b["identifier"])}</strong>{new}</div>'
                           f'<div class="meta">停止 {_ban_time(b["at"])}</div>'
                           f'<p>{html.escape(_mask(b.get("reason")) or ("停止済みのIDとの関連による停止" if b.get("source") == "linked" else "手動停止"))}</p>'
                           f'<div class="meta">停止後のアクセス {b.get("views", 0)}回 · 送信の試行 {b.get("attempts", 0)}回</div></a>')
        if not items:
            content.append('<p>利用停止中のIDはありません。</p>')
    else:
        b = selected
        cause = _ban_cause(b, items)
        rows = _ban_history(identifier)
        decisive = next((r for r in rows if _caused_ban(r, cause)), None)
        # 関連IDで行った元の書き込みも、正確なキーが一致したときだけ表示する。
        if not decisive and cause is not b and cause.get("conv_pk") and cause.get("conv_sk"):
            decisive = next((r for r in _ban_history(cause["identifier"]) if _caused_ban(r, cause)), None)
        content.append(f'<h2 class="ban-id">{html.escape(identifier)}</h2>'
                       f'<div class="meta">停止 {_ban_time(b["at"])} · {_mask({"auto":"自動停止", "manual":"手動停止", "linked":"関連IDによる停止"}.get(b.get("source"), "停止"))}</div>'
                       '<div id="review-status" class="meta" role="status">' + ('確認済み' if b.get('reviewed_at') else '確認状態を保存中…') + '</div>')
        content.append(f'<div class="ban-visits"><div><span>停止後のアクセス</span><strong>{b.get("views", 0)}<small>回</small></strong></div>'
                       f'<div><span>質問・評価の送信試行</span><strong>{b.get("attempts", 0)}<small>回</small></strong></div></div>'
                       f'<p class="meta">最終アクセス {_ban_time(b.get("seen_at"))}</p>')
        content.append('<section class="ban-cause"><h2>停止の判定</h2>'
                       f'<p>{html.escape(_mask(cause.get("reason")) or "理由の記録なし")}</p>')
        if cause is not b:
            content.append(f'<p class="meta">元の停止：<a href="{html.escape(_url(v="bans", u=cause["identifier"]))}">{html.escape(cause["identifier"])}</a></p>')
        if decisive:
            content.append(_ban_post(decisive, cause, True))
        else:
            content.append('<p class="note">判定元の書き込みを特定できる記録がありません。</p>')
            if cause.get("question"):
                content.append(f'<p>停止記録に残っている入力：{html.escape(_mask(cause["question"]))}</p>')
        content.append('</section>')
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(1, min(page, pages))
        content.append(f'<section><h2>このIDの書き込み <small>全期間・{len(rows)}件・新しい順</small></h2><div class="list">')
        content.extend(_ban_post(r, cause) for r in rows[(page-1)*PAGE_SIZE:page*PAGE_SIZE])
        if not rows:
            content.append('<p>保存された書き込みはありません。</p>')
        content.append('</div></section>')
        if pages > 1:
            content.append('<nav class="pager">')
            for pnum, label in ((page-1, '‹ 新しい50件'), (page+1, '古い50件 ›')):
                if 1 <= pnum <= pages:
                    content.append(f'<a href="{html.escape(_url(v="bans", u=identifier, p=pnum))}">{label}</a>')
            content.append(f'<span>{page} / {pages}</span></nav>')
        if not b.get("reviewed_at"):
            token = _sign({"purpose": "ban-review", "identifier": identifier, "at": b["at"], "exp": time.time() + 3600})
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>利用停止｜監視ダッシュボード</title><style>{CSS}</style></head>
<body><main class="wrap ban-wrap"><header class="top"><div><h1>利用停止</h1><div class="at">日本時間 {_ban_time(now.isoformat())} 時点</div></div></header>
<nav class="ban-nav"><a href="./">‹ 要約へ</a>{'<a href="?v=bans">利用停止の一覧へ</a>' if identifier else ''}</nav>
<div id="ban-content" data-review="{html.escape(token, quote=True)}">{''.join(content)}</div></main>{BAN_SCRIPT}</body></html>'''


BAN_SCRIPT = """<script>
(() => {
 let busy = false;
 async function review() {
  const root = document.querySelector('#ban-content'), token = root.dataset.review;
  if (!token || document.hidden) return;
  try {
   const r = await fetch('ban-reviewed', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({csrf:token}), signal:AbortSignal.timeout(12000)});
   if (!r.ok || r.redirected) throw new Error();
   root.dataset.review = ''; document.querySelector('#review-status').textContent = '確認済み';
  } catch (_) { document.querySelector('#review-status').textContent = '確認状態を保存できませんでした。再試行します。'; }
 }
 async function refresh() {
  if (busy || document.hidden) return;
  busy = true;
  try {
   const r = await fetch(location.href, {cache:'no-store', signal:AbortSignal.timeout(12000)});
   if (!r.ok || r.redirected) return;
   const doc = new DOMParser().parseFromString(await r.text(), 'text/html'), next = doc.querySelector('#ban-content');
   if (!next) return;
   const current = document.querySelector('#ban-content'), y = scrollY;
   const open = new Set([...current.querySelectorAll('details[open]')].map(e => e.dataset.row));
   current.replaceChildren(...next.childNodes); current.dataset.review = next.dataset.review;
   current.querySelectorAll('details').forEach(e => { e.open = open.has(e.dataset.row); });
   document.querySelector('header .at').textContent = doc.querySelector('header .at').textContent;
   window.scrollTo(0, y); await review();
  } catch (_) {} finally { busy = false; }
 }
 review();
 window.addEventListener('pageshow', e => { if (e.persisted) refresh(); });
 document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
 setInterval(refresh, 15000);
})();
</script>"""


def _owner_actors():
    item = ddb.get_item(TableName=AUDIENCE_TABLE, Key={"pk": {"S": "audience#owner"}}, ConsistentRead=True).get("Item", {})
    return set(item.get("actors", {}).get("SS", []))


def _audience(r, owners):
    actor = _actor(r)
    if actor in TEST_ACTORS or (r.get("pk") or "").startswith("e2e-"):
        return "test"
    if actor in owners:
        return "owner"
    return "general"


def _selected(r, u):
    actor = _actor(r)
    if not u or u == "all":
        return True
    if u in AUDIENCES:
        return r.get("audience", "general") == u
    if u == "google":
        return actor.startswith("user-")
    if u == "anonymous":
        return actor != "unknown" and not actor.startswith("user-")
    return _identifier(actor) == u


def _question_id(r):
    # 同文の質問だけをまとめる。伏せ字にした後の文を使うと別の質問が混ざる。
    return hashlib.sha256((r.get("question") or "").strip().encode()).hexdigest()[:16]


def report(day=None, u="general"):
    """画面と JSON が共有する1回の取得結果。費用は時台ごとの質問数で按分する。"""
    u = u or "general"
    now, start, rows = questions(day)
    mode, _, period_end = periods.bounds(day, now, COST_SINCE)
    end = min(start + datetime.timedelta(days=1), now) if mode == "day" else period_end
    m = _metrics(start, end, 3600)
    registered = _registered_users()
    owners = _owner_actors()
    groups = {}
    for r in rows:
        r["audience"] = _audience(r, owners)
        h = datetime.datetime.fromisoformat(r["created_at"]).astimezone(JST).replace(minute=0, second=0, microsecond=0)
        groups.setdefault(h, []).append(r)
        r["identifier"] = _identifier(_actor(r))
        r["question_id"] = _question_id(r)
    people = {_identifier(a): {"identifier": _identifier(a), "questions": 0, "estimated_usd": 0.0, "registered": True, **info}
              for a, info in (registered or {}).items()}
    costs = {k: 0.0 for k in LABELS}
    unallocated, hourly = 0.0, []
    h = start
    while h < end:
        rs = groups.get(h, [])
        selected = [r for r in rs if _selected(r, u)]
        _, brave, kb = _tool_costs(rs)
        hc = _cost(m, h, brave, kb, x=_x_cost(rs))
        # 検索2種類は回数が行に残るので、その利用者に直接紐づける。
        shared = {k: v for k, v in hc.items() if k not in ("brave", "kb", "x")}
        per_question = sum(shared.values()) / len(rs) if rs else 0.0
        if not rs:
            unallocated += sum(hc.values())
        for r in rs:
            _, b, k = _tool_costs([r])
            r["estimated_usd"] = per_question + b + k + _x_cost([r])
            person = people.setdefault(r["identifier"], {"audience": r["audience"], "identifier": r["identifier"], "questions": 0, "estimated_usd": 0.0})
            person["questions"] += 1
            person["estimated_usd"] += r["estimated_usd"]
        sc = hc  # 費用は表示対象によらず全件。個別の按分値は質問行に残す。
        for k, v in sc.items():
            costs[k] += v
        hourly.append({"hour": h, "sessions": len({r.get("pk") for r in selected if r.get("pk")}),
                       "users": len({_actor(r) for r in selected if _actor(r) != "unknown"}),
                       "questions": len(selected), "usd": sum(sc.values()),
                       "user_ids": {_actor(r) for r in selected if _actor(r) != "unknown"},
                       "session_ids": {r.get("pk") for r in selected if r.get("pk")}})
        h += datetime.timedelta(hours=1)
    selected = [r for r in rows if _selected(r, u)]
    firsts = [r["first_ms"] for r in selected if r.get("first_ms") is not None]
    tools, _, _ = _tool_costs(selected)
    active = {_actor(r) for r in rows if _actor(r).startswith("user-")}
    new_users = {_identifier(a) for a, info in (registered or {}).items()
                 if info.get("registered_at") and start <= datetime.datetime.fromisoformat(info["registered_at"]) < end}
    for person in people.values():
        person["new"] = person["identifier"] in new_users
        person["active"] = person["identifier"] in {_identifier(a) for a in active}
    d = {"now": now, "day": start, "end": end, "period": mode,
         "selection": periods.key(mode, start, now), "new_google": len(new_users) if registered is not None else None,
         "questions": len(selected),
         "users": len({_actor(r) for r in selected if _actor(r) != "unknown"}),
         "first_p50": statistics.median(firsts) if firsts else None,
         "rejected": _rejected(start, end), "tools": tools, "cost": costs,
         "total": sum(costs.values()), "pace": _pace(now),
         "feedback": {v: sum(r.get("feedback") == v for r in selected) for v in ("good", "bad")},
         "via": {v: sum(r.get("via") == v for r in selected) for v in VIA_LABELS},
         "registered_google": len(registered) if registered is not None else None,
         "active_google": len(active), "unallocated_usd": unallocated, "u": u, "owner_registered": bool(owners),
         "people": sorted(people.values(), key=lambda x: (-x["questions"], x["identifier"]))}
    total = {"users": d["users"], "sessions": len({r.get("pk") for r in selected if r.get("pk")}), "questions": len(selected), "usd": d["total"]}
    return d, list(reversed(hourly)), total, selected


def _people_html(d, view, f):
    ds, u = d["selection"], d.get("u") or "general"
    groups = [("利用区分", list(AUDIENCES.items())),
              ("ログイン種別", [("google", "Google登録者"), ("anonymous", "匿名利用者")])]
    individual = u not in {key for _, choices in groups for key, _ in choices}
    opts = "".join('<optgroup label="' + label + '">' + "".join(
        f'<option value="{key}"{" selected" if key == u else ""}>{name}</option>'
        for key, name in choices) + '</optgroup>' for label, choices in groups)
    people = list(d["people"])
    if individual and u not in {p["identifier"] for p in people}:
        people.append({"identifier": u, "questions": 0, "estimated_usd": 0})
    links = []
    for person in people:
        actor = person["identifier"]
        href = "?" + urllib.parse.urlencode({"d": ds, "v": "list", "f": f or "", "u": actor})
        links.append(f'<a href="{html.escape(href, quote=True)}" data-person="{html.escape(actor, quote=True)}">'
                     f'<span>{html.escape(actor)}</span><small>{person["questions"]}問 · 約{_yen(person["estimated_usd"])}</small></a>')
    owner_note = "登録あり" if d["owner_registered"] else "未登録"
    scope = "この日" if d["period"] == "day" else "この期間"
    stats = []
    for group, label, count in (("registered", "現在の登録総数", d["registered_google"]),
                                ("new", scope + "の新規登録", d["new_google"]),
                                ("active", scope + "の利用", d["active_google"])):
        number = str(count) + '<small>人</small>' if count is not None else '<span>取得できませんでした</span>'
        href = html.escape(_url(v="people", d=ds, g=group, u=u))
        stats.append(f'<a href="{href}"><span>{label}</span><strong>{number}</strong></a>')
    return f'''<div class="people-panel">
<div class="people-stats" id="people-counts"><div class="people-label">Google登録者</div><div class="people-pair">{"".join(stats)}</div></div>
<form class="people-filter" id="people-form" method="get"><input type="hidden" name="d" value="{ds}"><input type="hidden" name="v" value="{view}"><input type="hidden" name="f" value="{html.escape(f or '', quote=True)}"><input type="hidden" name="u" id="people-value" value="{html.escape(u, quote=True)}">
<label class="people-label" for="who">質問の表示対象</label><div class="people-select"><select id="who">{opts}<option value="person"{" selected" if individual else ""}>利用者を指定…</option></select></div>
<div class="people-search" id="person-search"{"" if individual else " hidden"}><label for="person-query">利用者IDを検索</label><input id="person-query" type="search" autocomplete="off" placeholder="G- または A- のID" value="{html.escape(u if individual else '', quote=True)}"><div class="people-results" id="person-results">{"".join(links)}</div><p class="people-empty" id="person-empty" role="status" hidden>該当する利用者はいません</p></div></form>
<div class="people-owner"><div class="people-owner-label"><span class="people-label">本人の登録</span><small id="owner-result" role="status">{owner_note}</small></div><button type="button" id="register-owner">このブラウザを登録</button></div>
<div class="people-footer"><span>費用・拒否・障害は全利用を集計</span><details class="people-help"><summary>集計について</summary><p>G- はGoogle登録者、A- は匿名のブラウザ。識別子は日をまたいで共通です。匿名の保存情報を消した場合やログイン前後は別として表示します。本人の登録は登録情報全体の有無を示します。</p></details></div>
</div>''' + PEOPLE_SCRIPT


PEOPLE_SCRIPT = """<script>
(() => {
 const who = document.getElementById('who'), search = document.getElementById('person-search');
 const input = document.getElementById('person-query'), form = document.getElementById('people-form');
 function filter() {
   const query = input.value.trim().toLowerCase();
   const links = [...document.querySelectorAll('#person-results a')];
   links.forEach(link => { link.hidden = !link.dataset.person.toLowerCase().includes(query); });
   document.getElementById('person-empty').hidden = links.some(link => !link.hidden);
 }
 who.addEventListener('change', () => {
   search.hidden = who.value !== 'person';
   if (who.value === 'person') { filter(); input.focus(); }
   else { document.getElementById('people-value').value = who.value; form.requestSubmit(); }
 });
 form.addEventListener('submit', event => { if (who.value === 'person') event.preventDefault(); });
 input.addEventListener('input', filter);
 input.addEventListener('keydown', event => {
   if (event.key === 'ArrowDown' || event.key === 'Enter') {
     event.preventDefault(); document.querySelector('#person-results a:not([hidden])')?.focus();
   } else if (event.key === 'Escape') { who.focus(); }
 });
 document.addEventListener('people-refreshed', filter);
 filter();
})();
</script>"""


def _owner_script():
    csrf = _sign({"purpose": "owner-registration", "exp": time.time() + 3600})
    return """<input id="owner-csrf" type="hidden" value=CSRF><script>
const ownerButton = document.getElementById('register-owner');
ownerButton.onclick = async () => {
  ownerButton.disabled = true;
  const status = document.getElementById('owner-result');
  try {
    const hash = async s => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)))).map(b=>b.toString(16).padStart(2,'0')).join('').slice(0,16);
    const actors = [];
    const anon = localStorage.getItem('minorun-anon-id');
    if (anon) actors.push(await hash(anon));
    const auth = JSON.parse(localStorage.getItem('minorun-auth') || 'null');
    if (auth && auth.access) {
      const part = auth.access.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
      const claims = JSON.parse(atob(part));
      if (claims.username) actors.push('user-' + await hash('user:' + claims.username));
    }
    if (!actors.length) throw new Error('先に同じブラウザでアプリを利用してください。');
    const res = await fetch('owner', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({actors,csrf:document.getElementById('owner-csrf').value})});
    if (!res.ok) throw new Error('登録できませんでした。再読み込みしてお試しください。');
    location.reload();
  } catch (e) { status.textContent = e.message; ownerButton.disabled = false; }
};
</script>""".replace("CSRF", json.dumps(csrf))


def _register_owner(event):
    headers = {k.lower(): v for k,v in (event.get("headers") or {}).items()}
    expected = urllib.parse.urlsplit(PUBLIC_BASE)
    if headers.get("origin") != f"{expected.scheme}://{expected.netloc}":
        return _html("forbidden", 403)
    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        data = json.loads(raw)
        csrf = _verify(data.get("csrf", ""))
        actors = data.get("actors")
        if (not csrf or csrf.get("purpose") != "owner-registration" or not isinstance(actors,list)
                or not 1 <= len(actors) <= 2 or not all(isinstance(a,str) and re.fullmatch(r"(?:user-)?[0-9a-f]{16}",a) for a in actors)):
            return _html("invalid request", 400)
    except (ValueError, TypeError, AttributeError):
        return _html("invalid request", 400)
    ddb.update_item(TableName=AUDIENCE_TABLE, Key={"pk":{"S":"audience#owner"}},
                    UpdateExpression="ADD actors :actors", ExpressionAttributeValues={":actors":{"SS":sorted(set(actors))}})
    return _html("ok")


def _mask(text):
    return PHONE.sub("［電話番号］", EMAIL.sub("［メール］", text or ""))


# ---- 画面 -------------------------------------------------------------------
# 1枚のページに「要約・1時間ごと・質問の一覧」を全部入れ、PC・スマホとも上のタブ（?v=）で1つだけ見せる。
# 質問の一覧は枠の中でスクロールさせず、ページ全体のスクロールで縦長の画面をそのまま使う（2026-10-26 みのるん指示）。

CSS = """:root{--bg:#f5f7fa;--card:#fff;--ink:#111827;--sub:#6b7280;--line:#e5e8ee;--soft:#eef4fd;--accent:#0a64d8;--warn:#c62828;--warn-soft:#fdecea;--good:#15803d;--good-soft:#e7f6ec}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
.wrap{max-width:1360px;margin:0 auto;padding:16px 16px 40px}
header.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:4px 0 14px;flex-wrap:wrap}
header.top h1{margin:0;font-size:18px;font-weight:700;letter-spacing:.01em}header.top .at{font-size:12px;color:var(--sub)}
.day{display:flex;align-items:center;gap:6px}.day b{font-size:15px;min-width:92px;text-align:center}
.btn{display:inline-block;padding:6px 12px;border-radius:999px;background:var(--card);border:1px solid var(--line);font-size:13px;color:var(--ink);box-shadow:0 1px 2px rgba(16,24,40,.04)}
.btn.off{visibility:hidden}.btn.quiet{border-color:transparent;background:none;box-shadow:none;color:var(--sub)}
nav.tabs{display:flex;gap:4px;padding:4px;background:#e9edf3;border-radius:12px;margin:0 0 14px}
nav.tabs a{flex:1;text-align:center;padding:8px 4px;border-radius:9px;font-size:14px;color:var(--sub);font-weight:600}
nav.tabs a.on{background:var(--card);color:var(--ink);box-shadow:0 1px 3px rgba(16,24,40,.08)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 1px 3px rgba(16,24,40,.05)}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.stat{padding:14px 16px}.stat .k{font-size:12px;color:var(--sub);font-weight:600}.stat .v{font-size:28px;font-weight:700;line-height:1.25;margin-top:2px;font-variant-numeric:tabular-nums}
.stat .s{font-size:12px;color:var(--sub);margin-top:2px}.stat.cost,.stat.wide{grid-column:1/-1}.stat.cost .v{color:var(--accent)}.stat.cost.warn .v{color:var(--warn)}
.person-link{font-family:monospace;overflow-wrap:anywhere}.qbody{min-width:0}
small{font-size:13px;color:var(--sub);font-weight:400}
.breakdown{padding:6px 16px;margin-top:10px}
h2{font-size:14px;margin:0;padding:14px 16px 8px;font-weight:700}h2 small{margin-left:6px}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
td,th{padding:9px 16px;border-top:1px solid var(--line);text-align:right;white-space:nowrap}td:first-child,th:first-child{text-align:left}
th{font-size:12px;color:var(--sub);font-weight:600;border-top:none;padding-top:4px}
tr.sum td{font-weight:700;background:var(--soft)}tr.zero td{color:#a0a7b4}
.breakdown td{padding:9px 0}.breakdown td:first-child{white-space:normal;overflow-wrap:anywhere}.breakdown h2{padding-left:0}
.list{padding:0 8px 8px}
details{border-top:1px solid var(--line)}details:first-of-type{border-top:none}
summary{list-style:none;padding:11px 8px;cursor:pointer;border-radius:10px}summary:hover{background:#f8fafc}summary::-webkit-details-marker{display:none}
summary{display:flex;gap:12px;align-items:flex-start}.qbody{flex:1;min-width:0}
.q{font-weight:600;overflow-wrap:anywhere}.meta{font-size:12px;color:var(--sub);margin-top:4px;overflow-wrap:anywhere}.meta .dot{margin:0 6px;color:#c3c8d1}
.meta .alert{color:var(--warn);font-weight:600}
.fb{flex:none;width:30px;height:30px;border-radius:50%;display:grid;place-items:center;margin-top:1px}.fb svg{width:16px;height:16px}
.fb.good{background:var(--good-soft);color:var(--good)}.fb.bad{background:var(--warn-soft);color:var(--warn)}
details.bad>summary{box-shadow:inset 3px 0 0 var(--warn)}
.filter{display:flex;gap:6px;padding:0 16px 10px;flex-wrap:wrap}.filter a{padding:4px 11px;border-radius:999px;border:1px solid var(--line);font-size:12.5px;color:var(--sub);background:var(--card)}
.filter a.on{background:var(--ink);border-color:var(--ink);color:#fff}.filter a b{font-weight:700;margin-left:4px}.filter a.hot:not(.on){border-color:#f2b8b5;color:var(--warn)}
.stat.fbcard .v{display:flex;gap:14px;align-items:center}.stat.fbcard .v span{display:inline-flex;align-items:center;gap:5px}.stat.fbcard .v svg{width:20px;height:20px}
.stat.fbcard .g{color:var(--good)}.stat.fbcard .b{color:var(--sub)}.stat.fbcard.hot{border-color:#f2b8b5;background:var(--warn-soft)}.stat.fbcard.hot .b{color:var(--warn)}
.stat .s a{font-weight:600}
.ans{margin:0 8px 12px;padding:12px 14px;background:#f8fafc;border-radius:10px;font-size:14px;line-height:1.75;overflow-wrap:anywhere}
.ans p{margin:0 0 8px}.ans p:last-child{margin:0}.ans ul{margin:0 0 8px;padding-left:20px}.ans li{margin:2px 0}
.ans a.src{display:inline-block;margin-left:4px;padding:0 7px;border-radius:999px;background:#e9edf3;color:var(--sub);font-size:11px;line-height:1.7;white-space:nowrap}
.ans code{background:#e9edf3;border-radius:4px;padding:0 4px;font-size:12.5px}
details.typed>summary{background:#fffbea}details.typed>summary:hover{background:#fff6d1}
.legend{display:flex;gap:12px;padding:0 16px 8px;font-size:12px;color:var(--sub)}.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;background:#fffbea;border:1px solid #f3e3a6;margin-right:4px;vertical-align:-1px}
.pager{display:flex;justify-content:space-between;align-items:center;padding:8px 16px 14px;font-size:13px;color:var(--sub)}
p.note{font-size:12px;color:var(--sub);margin:14px 4px 0}p.inline{padding:0 16px 12px;margin:0}
section.panel{display:none}section.panel.on{display:block}
@media (min-width:900px){
  .wrap{padding:24px 28px 48px}nav.tabs{display:none}section.panel{display:block}
  .stats{grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}.stat.cost,.stat.wide{grid-column:auto}
  .cols{display:grid;grid-template-columns:minmax(360px,5fr) 7fr;gap:16px;align-items:start;margin-top:16px}
  .breakdown tbody{display:grid;grid-template-columns:repeat(3,1fr);column-gap:36px}.breakdown tr{display:flex;justify-content:space-between;border-top:1px solid var(--line)}
  .breakdown td{border-top:none}.breakdown tr:nth-child(-n+3){border-top:none}
  .cols .panel{margin:0}.list{max-height:calc(100vh - 260px);overflow:auto}
}
section.panel + section.panel{margin-top:14px}
/* 余白で整理：承認済みの白基調・青アクセント */

:root{--bg:#fff;--ink:#1b2431;--sub:#647082;--line:#e9edf2;--accent:#1764dc;--soft:#f6f8fb}
body{line-height:1.6} .wrap{max-width:1440px;padding:32px 32px 48px}header.top{margin:0 0 28px}header.top h1{font-size:23px;font-weight:650;letter-spacing:-.035em}header.top .at{margin-top:6px}.btn{border-radius:8px;box-shadow:none;min-height:40px;padding:9px 13px}.day{gap:10px}

.card{box-shadow:none;border-radius:10px}.stat{min-width:0;padding:20px}.stat .v{font-size:36px;letter-spacing:-.055em;font-weight:600;margin:10px 0}.stat .v small{display:block;font-size:12px;letter-spacing:0;margin-top:5px}.stat .k{font-weight:500;letter-spacing:.02em}.stat .s{line-height:1.8}.stat.fbcard .v{gap:20px}.stat.fbcard.hot{background:#fff;border-color:var(--line)}.stat.fbcard.hot .b{color:var(--warn)}
.breakdown{margin-top:20px;padding:8px 20px;background:#fafbfc;border:0}.breakdown td{font-size:12px}.breakdown small{font-size:11px}
h2{font-size:16px;padding:22px 20px 16px;font-weight:600;letter-spacing:-.02em}h2 small{display:block;margin:4px 0 0;font-size:11px}td,th{padding:13px 20px}th{font-weight:400}tr.sum td{background:#f7f9fc}table{font-size:13px}
.filter{gap:5px;padding:0 20px 16px}.filter a{border:0;border-radius:6px;padding:6px 10px;background:#f3f5f8}.filter a.on{background:#eaf1ff;color:#155bc5}.filter a.hot:not(.on){background:#fff0ef;color:#b53636}
.list{padding:0 12px 12px}summary{padding:19px 8px;border-radius:6px;gap:18px}.q{font-size:14px;font-weight:500}.meta{font-size:11px;line-height:1.8;margin-top:7px}.legend{font-size:11px;padding:0 20px 14px}.ans{border-radius:6px;padding:16px;line-height:1.9}.fb{width:26px;height:26px}.fb svg{width:14px;height:14px}.person-link{font-family:inherit}.cols .panel+.panel{margin-top:0}
@media(min-width:900px){.stats{grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0}.cols{grid-template-columns:minmax(330px,4fr) 7fr;gap:32px;margin-top:32px;align-items:stretch}.cols>.panel{min-width:0;height:720px;min-height:0}.cols>.panel>.card{height:100%;display:flex;flex-direction:column;overflow:hidden}.cols>.panel>.card>h2,.cols .date-tabs,.cols .filter,.cols .legend,.cols .note{flex:none}.cols .hour-rows{flex:1;min-height:0;overflow:auto}.cols .list{flex:1;min-height:0;max-height:none;overflow:auto}.cols .pager{flex:none}}
@media(max-width:899px){.wrap{padding:24px 18px 32px}header.top{gap:16px}header.top h1{font-size:21px}.day{width:100%;justify-content:space-between;gap:4px}.day .quiet{padding-left:0}.stats{gap:0}.stat{padding:18px 12px}.stat .v{font-size:34px}.stat.cost,.stat.wide{grid-column:auto}.breakdown{padding:6px 12px}.breakdown td{white-space:normal}nav.tabs{background:transparent;padding:0;border-radius:0;border-bottom:1px solid var(--line);gap:12px;margin-bottom:22px}nav.tabs a{font-size:13px;padding:12px 0;border-radius:0}nav.tabs a.on{box-shadow:inset 0 -2px var(--accent);color:var(--accent);background:transparent}.cols{margin-top:20px}h2{padding:20px 16px 16px}.filter{padding:0 16px 16px}td,th{padding:12px 10px}.cols .panel+.panel{margin-top:0}}
.stats .stat{border:0;border-radius:0;border-bottom:1px solid var(--line)}.cols .card{border:0;border-top:1px solid var(--line);border-radius:0}.stats .stat:first-child .v{color:var(--accent)}@media(min-width:900px){.stats .stat{border-right:1px solid var(--line);border-bottom:0}.stats .stat:last-child{border-right:0}.stat{padding:14px 20px 20px}.stats .stat:first-child{padding-left:0}.cols h2{padding-left:0}.cols .filter{padding-left:0}.cols .legend{padding-left:0}}
/* 色を絞る（2026-10-25 みのるん指示「色がぐちゃぐちゃしてAIくさい」）：数字は墨1色。赤は対応が要るときの文字だけ。
   装飾の面の塗りと緑は使わない。自由入力を見分ける薄黄色は維持する。青はリンクと選択中の印だけ */
/* 数字の大きさを統一し、桁が多い金額はカードの幅で収める。 */
@media(min-width:900px){.stats{grid-template-columns:repeat(4,minmax(0,1fr))}.stats .stat:nth-child(4n){border-right:0}.stats .stat:nth-child(-n+4){border-bottom:1px solid var(--line)}}
@media(max-width:379px){.stat.cost{grid-column:1/-1}}
.stats .stat:first-child .v,.stat.cost .v{color:var(--ink)}.stat.cost.warn .v{color:var(--warn)}
.stat.fbcard .g,.stat.fbcard .b{color:var(--ink)}.stat.fbcard.hot .b{color:var(--warn)}.stat.fbcard .v svg{width:18px;height:18px;opacity:.55}
.stat .s a{font-weight:500;color:var(--sub);text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:3px}
a.stat{display:block;color:inherit}a.stat:hover{background:var(--soft)}.bancard .v small{display:inline;font-size:14px;margin-left:3px;color:var(--sub);letter-spacing:0}
.bancard .new{color:var(--warn);font-weight:600}.bancard.hot .v{color:var(--warn)}
.breakdown{background:transparent;border-top:1px solid var(--line);border-radius:0}tr.sum td{background:transparent;font-weight:600}
summary:hover{background:var(--soft)}
details.bad>summary{box-shadow:inset 2px 0 0 var(--warn)}
.fb.good,.fb.bad{background:none}.fb.good{color:var(--sub)}.fb.bad{color:var(--warn)}
.filter a,.filter a.on,.filter a.hot:not(.on){background:none;border:0;border-radius:0;padding:4px 0;margin-right:14px;color:var(--sub)}
.filter a.on{color:var(--ink);box-shadow:inset 0 -2px var(--ink)}.filter a b{font-weight:500;color:var(--sub)}.filter a.hot b{color:var(--warn)}
.ans{background:var(--soft)}.ans a.src{background:#fff;border:1px solid var(--line)}
/* 関連する数字をまとめ、広い画面では5区画を一列にする。 */
.stats{grid-template-columns:repeat(2,minmax(0,1fr))}
.stats .stat{padding:16px 12px;border-right:0;border-bottom:1px solid var(--line)}
.stats .usage,.stats .cost,.stats .rejected{grid-column:1/-1}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.usage .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}
.cost .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}
@media(max-width:379px){.cost .metrics{grid-template-columns:1fr}}
.metric{min-width:0}.stat .v{margin:5px 0;font-size:34px;white-space:nowrap}
.stat .metric.warn .v{color:var(--warn)}.stat .s{overflow-wrap:anywhere}
@media(min-width:900px){
 .stats{grid-template-columns:repeat(6,minmax(0,1fr))}
 .stats .usage,.stats .cost{grid-column:span 3}.stats .fbcard,.stats .bancard,.stats .rejected{grid-column:span 2}
 .stats .stat{padding:16px;border-bottom:1px solid var(--line);border-right:0}
 .stats .stat:first-child{padding-left:16px}
}
@media(min-width:1280px){
 .stats{grid-template-columns:3fr 3fr 2fr 2fr 2fr}
 .stats .stat{grid-column:auto;border-bottom:0;border-right:1px solid var(--line);padding:12px 16px}
 .stats .stat:nth-child(-n+4){border-bottom:0}.stats .stat:nth-child(4n){border-right:1px solid var(--line)}
 .stats .stat:last-child{border-right:0}.stats .stat:first-child{padding-left:0}
}

.people-panel{background:#f7f9fb;border-radius:12px;padding:20px 24px 14px;margin-bottom:22px;display:grid;grid-template-columns:1fr 1.1fr .9fr;gap:18px 32px;align-items:start}
.people-panel *{box-sizing:border-box}.people-label{font-size:13px;font-weight:600;color:#303946;display:block;margin:0 0 10px;line-height:20px}
.people-pair{display:flex;gap:32px}.people-pair>div{display:flex;align-items:baseline;gap:12px}.people-pair span{font-size:12px;color:#647082;white-space:nowrap}.people-pair strong{font-size:22px;line-height:44px;font-weight:600;white-space:nowrap}.people-pair strong small{font-size:12px;margin-left:4px}
.people-filter,.people-owner{min-width:0}.people-select{position:relative}.people-select:after{content:'';pointer-events:none;position:absolute;width:7px;height:7px;border-right:1.5px solid #647082;border-bottom:1.5px solid #647082;transform:rotate(45deg);right:17px;top:16px}
.people-panel select,.people-panel input{appearance:none;-webkit-appearance:none;display:block;width:100%;height:44px;background:#fff;border:1px solid #d7dde5;border-radius:8px;padding:0 40px 0 14px;color:#243042;font:inherit;font-size:16px;box-shadow:0 1px 2px #18253505}.people-panel select:focus-visible,.people-panel input:focus-visible,.people-panel button:focus-visible{outline:2px solid #1764dc;outline-offset:2px}
.people-owner-label{display:flex;justify-content:space-between;align-items:baseline;gap:12px}.people-owner-label small{font-size:12px}.people-owner button{width:100%;height:44px;border-radius:8px;border:1px solid #d7dde5;background:transparent;color:#303946;font:inherit;font-size:13px;cursor:pointer}
.people-footer{grid-column:1/-1;border-top:1px solid #e4e8ee;padding-top:11px;display:flex;justify-content:space-between;gap:16px;font-size:11px;color:#647082}.people-footer details{border:0;max-width:460px}.people-footer summary{padding:0;font-size:11px;display:block;white-space:nowrap}.people-footer summary:after{content:' ＋'}.people-footer details[open] summary:after{content:' −'}.people-footer p{font-size:12px;line-height:1.7;margin:8px 0 0}
.people-search{margin-top:12px}.people-search label{display:block;font-size:12px;margin-bottom:6px}.people-results{margin-top:6px;max-height:180px;overflow:auto;background:white;border:1px solid #d7dde5;border-radius:8px}.people-results a{display:flex;justify-content:space-between;width:100%;padding:12px;border:0;background:white;font:inherit;font-size:12px;text-align:left;cursor:pointer}.people-results a:hover{background:#edf2f7}
@media(max-width:1050px) and (min-width:700px){.people-panel{grid-template-columns:1.2fr 1fr;gap:18px 24px}.people-owner{grid-column:1/-1;display:flex;align-items:center;gap:20px}.people-owner-label{gap:18px}.people-owner-label .people-label{margin:0}.people-owner button{width:auto;padding:0 20px}}
@media(max-width:699px){.people-panel{grid-template-columns:1fr;padding:16px;gap:14px}.people-pair{display:grid;grid-template-columns:1fr 1fr;gap:18px}.people-pair>div{justify-content:space-between;gap:8px}.people-pair strong{line-height:32px}.people-label{margin-bottom:8px}.people-footer{display:flex;flex-wrap:wrap;gap:8px}.people-footer details{margin:0}.people-owner{padding-top:12px;border-top:1px solid #e4e8ee;display:grid;grid-template-columns:1fr auto;align-items:center;gap:12px}.people-owner-label{display:block}.people-owner-label .people-label{margin-bottom:0}.people-owner button{width:auto;padding:0 12px;font-size:12px}}

.people-panel [hidden]{display:none!important}.people-panel form{margin:0}.people-results a{color:var(--ink);text-decoration:none;gap:12px;align-items:baseline}.people-results a span{overflow-wrap:anywhere;min-width:0}.people-results a small{font-size:11px;white-space:nowrap}.people-empty{font-size:12px;color:var(--sub);margin:8px 0 0}.people-owner-label{flex-wrap:wrap}.people-owner-label small{overflow-wrap:anywhere}.people-pair>div{min-width:0}.people-pair strong>span{white-space:normal;font-size:12px}.people-pair strong{font-variant-numeric:tabular-nums}
@media(min-width:700px){.people-pair{gap:20px;flex-wrap:wrap}}@media(max-width:360px){.people-pair{gap:14px}.people-pair>div{gap:6px}.people-pair span{white-space:normal}.people-owner button{max-width:100%}}


/* PC もスマホと同じ上のタブで1画面ずつ見せる。一覧と1時間ごとは内側でスクロールさせず、ページごと縦に伸ばす。 */
nav.tabs{display:flex;background:transparent;padding:0;border-radius:0;border-bottom:1px solid var(--line);gap:12px;margin-bottom:22px}
nav.tabs a{font-size:13px;padding:12px 0;border-radius:0;background:transparent}nav.tabs a.on{box-shadow:inset 0 -2px var(--accent);color:var(--accent);background:transparent}
section.panel{display:none}section.panel.on{display:block}
@media(min-width:900px){
 nav.tabs{justify-content:flex-start;gap:32px;margin-top:8px}nav.tabs a{flex:none;font-size:14px;padding:12px 2px}
 .cols{display:block;margin-top:0}.cols>.panel{height:auto}.cols>.panel>.card{height:auto;display:block;overflow:visible}
 .cols .list,.cols .hour-rows{max-height:none;overflow:visible}.ans{max-width:920px}
}
"""


CSS += """
/* タイトル・日付・ログアウトを別々の領域へ置く。日付は画面の中央にそろえる。 */
header.dashboard-top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px 20px;align-items:start}
.dashboard-title{min-width:0}
header.top h1 span{display:inline-block}
header.dashboard-top .logout{grid-column:2;grid-row:1;justify-self:end;white-space:nowrap}
header.dashboard-top .day{grid-column:1/-1;grid-row:2;width:auto;justify-self:center;justify-content:center;gap:10px}
@media(max-width:599px){
 header.dashboard-top .logout{font-size:12px;padding:8px 0}
 header.dashboard-top h1{font-size:20px;line-height:1.5}
}
#hours-panel{min-width:0}
.date-tabs{display:flex;gap:20px;overflow-x:auto;scrollbar-width:thin;margin:0 16px 12px;padding:0;overscroll-behavior-x:contain}
.date-tabs a{flex:none;white-space:nowrap;padding:10px 0;color:var(--sub);font-size:13px;text-decoration:none;border-bottom:2px solid transparent}
.date-tabs a[aria-current=date]{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.date-tabs a:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
"""

REFRESH_SCRIPT = """<script id="dashboard-refresh">
(() => {
  let busy = false;
  // 今日のURLを日付固定にしない。既存の当日リンクから開いた場合も引き継ぐ。
  if (document.body.dataset.followToday === 'true') {
    const url = new URL(location.href);
    url.searchParams.delete('d');
    history.replaceState(null, '', url);
  }
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      let url = new URL(location.href);
      let response = await fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(12000)});
      if (!response.ok || response.redirected) return;
      let next = new DOMParser().parseFromString(await response.text(), 'text/html');
      const changedDay = next.body.dataset.day && next.body.dataset.day !== document.body.dataset.day;
      if (changedDay && (url.searchParams.has('p') || url.searchParams.has('q'))) {
        url.searchParams.delete('p'); url.searchParams.delete('q');
        response = await fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(12000)});
        if (!response.ok || response.redirected) return;
        next = new DOMParser().parseFromString(await response.text(), 'text/html');
      }
      const selectors = ['#summary-panel', '#people-panel', '#errors-panel', '#hours-panel', '#list-panel', 'header .at', '#people-counts', 'header .day', 'nav.tabs'];
      if (selectors.some(s => !next.querySelector(s))) return;
      if (next.querySelector('#owner-csrf')) document.querySelector('#owner-csrf').value = next.querySelector('#owner-csrf').value;
      const search = document.querySelector('#person-search');
      if (!search.contains(document.activeElement) && next.querySelector('#person-results')) {
        document.querySelector('#person-results').replaceChildren(...next.querySelector('#person-results').childNodes);
        document.dispatchEvent(new Event('people-refreshed'));
      }
      const list = document.querySelector('.list');
      // 開いている回答と、一覧内・ページ全体の読んでいる位置を保つ。
      const opened = new Set(changedDay ? [] : [...document.querySelectorAll('.list details[open]')].map(e => e.dataset.row));
      const datesLeft = document.querySelector('.date-tabs')?.scrollLeft || 0;
      const anchor = [...document.querySelectorAll('.list details')].find(e => e.getBoundingClientRect().bottom > Math.max(0, list.getBoundingClientRect().top));
      const anchorKey = anchor?.dataset.row, anchorTop = anchor?.getBoundingClientRect().top;
      const listTop = list?.scrollTop || 0, pageY = window.scrollY;
      // 展開中の行がページ境界から落ちる場合は、一覧を次回まで保つ。
      const nextKeys = new Set([...next.querySelectorAll('.list details')].map(e => e.dataset.row));
      for (const selector of selectors) {
        if (selector === 'header .day' && document.querySelector(selector).contains(document.activeElement)) continue;
        if (selector === '#list-panel' && [...opened].some(key => !nextKeys.has(key))) continue;
        document.querySelector(selector).replaceChildren(...next.querySelector(selector).childNodes);
      }
      document.body.dataset.day = next.body.dataset.day;
      document.body.dataset.followToday = next.body.dataset.followToday;
      document.querySelector('#people-form [name=d]').value = next.querySelector('#people-form [name=d]').value;
      if (changedDay) history.replaceState(null, '', url);
      if (!changedDay && document.querySelector('.date-tabs')) document.querySelector('.date-tabs').scrollLeft = datesLeft;
      document.querySelectorAll('.list details').forEach(e => { e.open = opened.has(e.dataset.row); });
      const newList = document.querySelector('.list');
      newList.scrollTop = changedDay ? 0 : listTop;
      window.scrollTo(0, pageY);
      const newAnchor = [...newList.querySelectorAll('details')].find(e => e.dataset.row === anchorKey);
      if (newAnchor) {
        const delta = newAnchor.getBoundingClientRect().top - anchorTop;
        if (getComputedStyle(newList).overflowY === 'auto') newList.scrollTop += delta;
        else if (pageY > 0) window.scrollTo(0, pageY + delta);
      }
    } catch (_) { /* 一時的な通信失敗は、表示を保って次の周期で再試行する。 */ }
    finally { busy = false; }
  }
  window.addEventListener("pageshow", e => { if (e.persisted) refresh(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(refresh, 15000);
})();
</script>"""


_THUMB = ('<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76'
          'a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>')


def _thumb(kind):
    flip = ' transform="rotate(180 12 12)"' if kind == "bad" else ""
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><g{flip}>{_THUMB}</g></svg>')


# 質問の一覧の絞り込み（?f=）
FILTERS = {None: "すべて", "rated": "評価あり", "bad": "良くない", "typed": "自由入力", "banned": "利用停止"}


def _keep(r, f, banned=frozenset()):
    if f == "banned":
        return _identifier(_actor(r)) in banned
    if f == "rated":
        return r.get("feedback") in ("good", "bad")
    if f == "bad":
        return r.get("feedback") == "bad"
    if f == "typed":
        return r.get("via") == "typed"
    return True


# 質問の出どころ（画面が送る via）。2026-10-25 午後より前の行には無い
VIA_LABELS = {"typed": "自由入力", "ai": "AIの候補", "starter": "用意した質問", "link": "リンク"}


def _md(text):
    """回答の Markdown を最小限だけ HTML にする（段落・箇条書き・太字・コード・出典のリンク）。先に全体をエスケープする"""
    def inline(s):
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a class="src" href="\2" target="_blank" rel="noopener">\1</a>', s)
    out = []
    for block in re.split(r"\n\s*\n", html.escape(text or "").strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        if lines and all(re.match(r"\s*[-*・] ", l) for l in lines):
            out.append("<ul>" + "".join(f"<li>{inline(re.sub(r'^\s*[-*・] ', '', l))}</li>" for l in lines) + "</ul>")
        elif lines:
            out.append("<p>" + "<br>".join(inline(l) for l in lines) + "</p>")
    return "".join(out)


def _url(**q):
    q = {k: v for k, v in q.items() if v not in (None, "", 1)}
    return ("?" + urllib.parse.urlencode(q)) if q else "./"


def _yen(usd):
    return f"{usd * YEN:,.0f}円"


def _summary_html(d, ban=None):
    def usd(v, decimals=0):
        return f"${v:,.{decimals}f}<small>（約{_yen(v)}）</small>"
    rej = sum(d["rejected"].values()) if d["rejected"] is not None else "—"
    warn = " warn" if d["total"] >= ALERT_USD else ""
    p = d.get("pace") or {}

    def metric(label, key, extra=""):
        v = p.get(key)
        return f'<div class="metric{extra}"><div class="k">{label}</div><div class="v">{usd(v) if v is not None else "—"}</div></div>'
    pace = (metric("公開から現在までの累計", "total_usd") + metric("月額予測（直近7日平均）", "month_usd", " monthly"))
    good, bad = d["feedback"]["good"], d["feedback"]["bad"]
    ds = d.get("selection", "" if d.get("follow_today") else d["day"].strftime("%Y-%m-%d"))
    fb_link = (f'<a href="{html.escape(_url(v="list", d=ds, f="bad", u=d.get("u")))}">良くないを見る ›</a>' if bad
               else (f'<a href="{html.escape(_url(v="list", d=ds, f="rated", u=d.get("u")))}">評価ありを見る ›</a>' if good else "まだ評価なし"))
    fb_card = (f'<div class="card stat fbcard{" hot" if bad else ""}"><div class="k">この日の回答の評価</div>'
               f'<div class="v"><span class="g">{_thumb("good")}{good}</span><span class="b">{_thumb("bad")}{bad}</span></div>'
               f'<div class="s">{fb_link}</div></div>')
    ban = ban or {"total": 0, "new": 0, "day": None}
    ban_sub = (f'<span class="new">新着 {ban["new"]}</span>' if ban["new"] else "")
    ban_card = (f'<a class="card stat bancard{" hot" if ban["new"] else ""}" href="{html.escape(_url(v="bans"))}">'
                f'<div class="k">現在の利用停止</div><div class="v">{ban["total"]}<small>ID</small></div><div class="s">{ban_sub}</div></a>'
                if ban["total"] else
                '<div class="card stat bancard"><div class="k">現在の利用停止</div><div class="v">0<small>ID</small></div></div>')
    cost_rows = "".join(f"<tr><td>{LABELS[k]}</td><td>{usd(v, 2)}</td></tr>" for k, v in sorted(d["cost"].items(), key=lambda x: -x[1]))
    return f"""<div class="stats">
<div class="card stat usage"><div class="metrics"><div class="metric"><div class="k">この日の質問</div><div class="v">{d['questions']}</div></div><div class="metric"><div class="k">この日の利用者</div><div class="v">{d['users']}</div></div></div></div>
<div class="card stat cost"><div class="metrics"><div class="metric{warn}"><div class="k">この日の概算コスト</div><div class="v">{usd(d['total'])}</div></div>{pace}</div></div>
{fb_card}
{ban_card}
<div class="card stat rejected"><div class="k">この日に断った数（全利用）</div><div class="v">{rej}</div></div>
</div>
<div class="card breakdown"><h2>この日の費用内訳</h2><table>{cost_rows}</table></div>"""


def _hours_html(rows, total, date_tabs="", multi=False):
    def cell(usd):
        return f"{_yen(usd)} <small>${usd:,.2f}</small>"
    lines = [f'<tr class="sum"><td>合計</td><td>{total["users"]}</td><td>{total["sessions"]}</td><td>{total["questions"]}</td><td>{cell(total["usd"])}</td></tr>']
    if multi:
        daily = {}
        for r in rows:
            bucket = daily.setdefault(r["hour"].date(), {"hour": r["hour"], "questions": 0, "usd": 0, "users_set": set(), "sessions_set": set()})
            bucket["questions"] += r["questions"]
            bucket["usd"] += r["usd"]
            bucket["users_set"].update(r.get("user_ids", []))
            bucket["sessions_set"].update(r.get("session_ids", []))
        rows = [dict(r, users=len(r["users_set"]), sessions=len(r["sessions_set"])) for r in daily.values()]
    for r in rows:
        zero = ' class="zero"' if not r["questions"] else ""
        lines.append(f'<tr{zero}><td>{periods.date_label(r["hour"]) if multi else str(r["hour"].hour) + "時台"}</td><td>{r["users"]}</td><td>{r["sessions"]}</td><td>{r["questions"]}</td><td>{cell(r["usd"])}</td></tr>')
    return (f'<div class="card"><h2>{"日ごと" if multi else "1時間ごと"}</h2>{date_tabs}<div class="hour-rows"><table><tr><th>{"日付" if multi else "時台"}</th><th>セッション</th><th>会話</th><th>質問</th><th>全利用の費用</th></tr>{"".join(lines)}</table></div>'
            '<p class="note inline">セッションは識別できた利用者の数（合計は選択期間で重複を除く）。ログイン前後・匿名の別ブラウザは別に数える。会話は会話IDの数。質問0件の時台にも、会話の後処理や他のアプリの分が少し乗ることがある。</p></div>')


def _list_html(start, all_rows, page, f, u=None, qid=None, banned=frozenset(), follow_today=False, selection=None, multi=False):
    f = f if f in FILTERS else None
    rows = [r for r in all_rows if _keep(r, f, banned) and (not qid or _question_id(r) == qid)]
    d = selection if selection is not None else ("" if follow_today else start.strftime("%Y-%m-%d"))
    counts = {k: sum(1 for r in all_rows if _keep(r, k, banned)) for k in FILTERS}
    filt = "".join(
        f'<a class="{" ".join(c for c in ("on" if k == f else "", "hot" if k in ("bad", "banned") and counts[k] else "") if c)}" '
        f'href="{html.escape(_url(v="list", d=d, f=k, u=u, q=qid))}">{n}<b>{counts[k]}</b></a>'
        for k, n in FILTERS.items() if k != "banned" or counts[k] or f == "banned")
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    page = min(max(page, 1), pages)
    items = []
    for r in rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]:
        t = datetime.datetime.fromisoformat(r["created_at"]).strftime("%H:%M")
        if multi:
            t = periods.date_label(datetime.datetime.fromisoformat(r["created_at"])) + " " + t
        parts = [t, AUDIENCES.get(r.get("audience"), ""), VIA_LABELS.get(r.get("via"), ""), "事前回答" if r.get("prebaked") else "", r.get("topic") or "", "・".join(x for x in (r.get("tools") or []) if x),
                 f"{len(r.get('answer') or '')}字"]
        meta = '<span class="dot">·</span>'.join(html.escape(x) for x in parts if x)
        identifier = _identifier(_actor(r))
        same = [x for x in all_rows if _question_id(x) == _question_id(r)]
        same_people = len({_actor(x) for x in same if _actor(x) != "unknown"})
        person = f'<a class="person-link" href="{html.escape(_url(v="list", d=d, u=identifier, f=f))}">{html.escape(identifier)}</a>' if identifier != "不明" else "識別子不明"
        duplicate = f'<a href="{html.escape(_url(v="list", d=d, u=u, q=_question_id(r)))}">同じ質問 {len(same)}件・{same_people}人</a>' if len(same) > 1 else ""
        meta = person + '<span class="dot">·</span>' + meta
        if duplicate:
            meta += '<span class="dot">·</span>' + duplicate
        meta += '<span class="dot">·</span>' + _model_cost_label(r)
        if identifier in banned:
            meta = '<span class="alert">利用停止中</span><span class="dot">·</span>' + meta
        if r.get("flag") and r["flag"] != "normal":
            meta = (f'<span class="alert">{html.escape(FLAG_LABELS.get(r["flag"], r["flag"]))}'
                    f'{"：" + html.escape(r["flag_reason"]) if r.get("flag_reason") else ""}</span><span class="dot">·</span>') + meta
        fb = r.get("feedback")
        mark = (f'<span class="fb {fb}" title="{"良い" if fb == "good" else "良くない"}">{_thumb(fb)}</span>'
                if fb in ("good", "bad") else "")
        cls = " ".join(c for c in ("typed" if r.get("via") == "typed" else "", "bad" if fb == "bad" else "") if c)
        row_key = html.escape(str(r.get("pk", "")) + "|" + str(r.get("sk", r["created_at"])), quote=True)
        items.append(f"""<details data-row="{row_key}"{f' class="{cls}"' if cls else ""}><summary><div class="qbody"><div class="q">{html.escape(_mask(r.get('question')))}</div>
<div class="meta">{meta}</div></div>{mark}</summary>
<div class="ans">{_md(_mask(r.get('answer')))}</div></details>""")
    pager = ""
    if pages > 1:
        prev = f'<a class="btn" href="{html.escape(_url(v="list", d=d, f=f, p=page - 1, u=u, q=qid))}">‹ 新しい{PAGE_SIZE}件</a>' if page > 1 else "<span></span>"
        nxt = f'<a class="btn" href="{html.escape(_url(v="list", d=d, f=f, p=page + 1, u=u, q=qid))}">古い{PAGE_SIZE}件 ›</a>' if page < pages else "<span></span>"
        pager = f'<div class="pager">{prev}<span>{page} / {pages}</span>{nxt}</div>'
    empty = "この日の質問はまだありません。" if not f else f"この日は「{FILTERS[f]}」の質問がありません。"
    body = "".join(items) or f'<p class="note inline">{empty}</p>'
    return (f'<div class="card"><h2>質問の一覧<small>新しい順・タップで回答</small></h2>'
            f'<div class="filter">{filt}</div>'
            + (f'<p class="note inline">同じ質問で絞り込み中 <a href="{html.escape(_url(v="list", d=d, f=f, u=u))}">解除</a></p>' if qid else "") +
            f'<div class="list">{body}</div>{pager}</div>')


def _date_tabs(now, start, view, u, f):
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    links = []
    for offset in range(7):
        day = today - datetime.timedelta(days=offset)
        label = f'{day:%-m/%-d}（{"月火水木金土日"[day.weekday()]}）'
        if offset == 0:
            label = "今日 " + label
        current = ' aria-current="date"' if day.date() == start.date() else ""
        url = _url(v=view, d=day.strftime("%Y-%m-%d") if offset else None, u=u, f=f)
        links.append(f'<a href="{html.escape(url)}"{current}>{label}</a>')
    return '<nav class="date-tabs" aria-label="集計日">' + "".join(links) + '</nav>'


def _period_errors(start, end, now):
    # 記録の保持期間は90日。日別キーを使い、期間外の全表走査を避ける。
    from concurrent.futures import ThreadPoolExecutor
    day = max(start, (now - datetime.timedelta(days=90)).replace(hour=0, minute=0, second=0, microsecond=0))
    days = []
    while day < end:
        days.append(day.date().isoformat())
        day += datetime.timedelta(days=1)
    if not days:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_errors, days))
    if any(r is None for r in results):
        return None
    return sorted((r for result in results for r in result), key=lambda r: r['at'], reverse=True)


def _people_list(d, group):
    group = group if group in ('registered', 'new', 'active') else 'registered'
    titles = {'registered': '現在のGoogle登録者', 'new': '選択期間の新規登録者', 'active': '選択期間に利用したGoogle登録者'}
    people = [p for p in d['people'] if p.get(group)]
    content = []
    for person in people:
        href = html.escape(_url(v='list', d=d['selection'], u=person['identifier']))
        registered = person.get('registered_at')
        at = periods.date_label(datetime.datetime.fromisoformat(registered)) if registered else '不明'
        content.append(f'<a class="user-row" href="{href}"><strong>{html.escape(person["identifier"])}</strong><span>登録日 {at}</span><span>選択期間 {person["questions"]}問 ›</span></a>')
    missing = group in ('registered', 'new') and d['registered_google'] is None
    body = ''.join(content) or ('<p>登録情報を取得できませんでした。</p>' if missing else '<p>該当する利用者はいません。</p>')
    tabs = ''.join(f'<a class="btn" href="{html.escape(_url(v="people", d=d["selection"], g=g, u=d["u"]))}"' + (' aria-current="page"' if group == g else '') + f'>{title}</a>' for g, title in (('registered', '総登録者'), ('new', '新規登録者'), ('active', '利用者')))
    return f'<div class="card user-directory"><h2>{titles[group]} <small>{"—" if missing else len(people)}人</small></h2><nav>{tabs}</nav>{body}</div>'


# 集計に数秒かかるので、押した瞬間にボタンを選択状態へ替えて、読み込み中だと分かるようにする。
PERIOD_PRESS = html.escape("const a=event.target.closest('a');if(!a)return;this.querySelectorAll('a').forEach(x=>x.removeAttribute('aria-current'));a.setAttribute('aria-current','true');document.querySelector('.period-nav')?.classList.add('loading')")


def _period_controls(d, view, u, f, group):
    mode, start, now = d['period'], d['day'], d['now']
    def href(m, at):
        if m == 'all':
            key = 'all'
        else:
            _, s, _ = periods.bounds(m + ':' + at.date().isoformat() if m != 'day' else at.date().isoformat(), now, COST_SINCE)
            key = periods.key(m, s, now)
        return html.escape(_url(v=view, d=key, u=u, f=f, g=group if view == 'people' else None))
    # 単位を替えるときは、今日を含む期間なら今日を、過去の期間ならその初日を起点にする。
    anchor = now if mode == 'all' or d['end'] >= now else start
    units = ''.join(f'<a href="{href(key, anchor)}"' + (' aria-current="true"' if key == mode else '') + f'>{label}</a>'
                    for key, label in (('day', '日'), ('week', '週'), ('month', '月'), ('all', '全期間')))
    if mode == 'all':
        nav = '<b>公開から現在まで</b>'
    else:
        words = {'day': ('前日', '翌日'), 'week': ('前週', '翌週'), 'month': ('前月', '翌月')}[mode]
        prev, nxt = periods.shift(mode, start, -1), periods.shift(mode, start, 1)
        back = (f'<a class="step" href="{href(mode, prev)}" aria-label="{words[0]}">‹</a>' if start > COST_SINCE
                else '<span class="step off" aria-hidden="true">‹</span>')
        fwd = (f'<a class="step" href="{href(mode, nxt)}" aria-label="{words[1]}">›</a>' if nxt <= now
               else '<span class="step off" aria-hidden="true">›</span>')
        nav = f'{back}<b>{periods.short_label(mode, start, d["end"])}</b>{fwd}'
    quick = _date_tabs(now, start, view, u, f) if mode == 'day' else ''
    return (f'<nav class="period-units" aria-label="集計期間" onclick="{PERIOD_PRESS}">{units}</nav>'
            f'<div class="period-nav">{nav}</div>{quick}')


def render(view, day, page, f=None, u=None, qid=None, group="registered"):
    d, hrs, total, rows = report(day, u)
    u = d["u"]
    now, start = d["now"], d["day"]
    ban = _ban_summary(bans(), now)
    errors = _period_errors(start, d["end"], now)
    view = view if view in ("hours", "list", "errors", "people") else "sum"
    on = lambda v: " on" if v == view else ""  # noqa: E731
    multi = d["period"] != "day"
    follow_today = not multi and start.date() == now.date()
    d["follow_today"] = follow_today
    ds = d["selection"]
    date_tabs = ""
    tabs = "".join(f'<a class="{on(v).strip()}" href="{html.escape(_url(v=None if v == "sum" else v, d=ds, u=u, f=f, q=qid))}">{n}</a>'
                   for v, n in (("sum", "要約"), ("hours", "日ごと" if multi else "1時間ごと"), ("list", "質問の一覧")))
    keep = None if view == "sum" else view
    controls = _period_controls(d, view, u, f, group)
    summary_html = _summary_html(d, ban)
    list_html = _list_html(start, rows, page, f, u, qid, ban["ids"], follow_today, ds, multi)
    if multi:
        summary_html = summary_html.replace("この日の", "この期間の").replace("この日に", "この期間に")
        list_html = list_html.replace("この日の", "この期間の").replace("この日は", "この期間は")
    error_summary = error_card(errors, ds).replace("この日の", "この期間の" if multi else "この日の")
    error_list = error_details(errors).replace("この日の", "この期間の" if multi else "この日の")
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<meta name="theme-color" content="#ffffff"><title>監視ダッシュボード</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2032%2032%22%3E%3Crect%20width%3D%2232%22%20height%3D%2232%22%20rx%3D%227%22%20fill%3D%22%23c55a11%22%2F%3E%3Cpath%20d%3D%22M8%2024V17M16%2024V8M24%2024V12%22%20fill%3D%22none%22%20stroke%3D%22white%22%20stroke-width%3D%224%22%20stroke-linecap%3D%22round%22%2F%3E%3C%2Fsvg%3E"><style>{CSS}</style></head><body data-day="{start:%Y-%m-%d}" data-follow-today="{str(follow_today).lower()}"><div class="wrap">
<header class="top dashboard-top"><div class="dashboard-title"><h1><span>みのるんエージェント</span> <span>監視ダッシュボード</span></h1><div class="at">日本時間 {now:%-m/%-d}（{"月火水木金土日"[now.weekday()]}）{now:%H:%M:%S} 時点</div></div>
<a class="btn quiet logout" href="logout">ログアウト</a><div class="day">{controls}</div></header>
<nav class="tabs">{tabs}</nav>
{_people_html(d, view, f)}
{_owner_script()}
<section id="summary-panel" class="panel{on('sum')}">{error_summary}{summary_html}</section>
<div class="cols"><section id="hours-panel" class="panel{on('hours')}">{_hours_html(hrs, total, date_tabs, multi)}</section>
<section id="list-panel" class="panel{on('list')}">{list_html}</section></div>
<section id="errors-panel" class="panel{on('errors')}">{error_list}</section>
<section id="people-panel" class="panel{on('people')}">{_people_list(d, group)}</section>
<p class="note">質問一覧のモデル費用は、回答・候補生成・判定・要約などの記録済み使用量から計算した概算です。事前回答はモデルを呼ばないため0円。記録がない・不完全な質問は未計測と表示します。サーバー・検索などの費用は含みません。利用者別の費用は時間帯の費用を質問数で按分した目安です（Braveとナレッジベースの検索は記録回数から計算）。質問0件の時間帯は割り当てず、全体の費用だけに含めます。<br>コストは概算：Bedrock のトークン数はアカウント全体の数字で、同じアカウントの他のアプリの分も入る。評価（Evaluations）・WAF・ログは含まない。確定値は AWS Budgets（1日 $30）が数時間遅れで知らせる。1ドル{YEN:.0f}円で換算。</p>
</div>{REFRESH_SCRIPT}</body></html>"""


def as_json(day, u="all"):
    """外部のダッシュボードがブラウザから読む形。d の日（省くと今日）の要約・1時間ごと・質問の一覧・要注意の一覧"""
    d, hrs, total, rows = report(day, u)
    u = d["u"]
    now, start = d["now"], d["day"]

    def item(r):
        return {"at": r["created_at"], "question": _mask(r.get("question")), "answer": _mask(r.get("answer")),
                "topic": r.get("topic"), "tools": r.get("tools") or [], "first_ms": r.get("first_ms"),
                "answer_chars": len(r.get("answer") or ""), "flag": r.get("flag"), "flag_reason": r.get("flag_reason"),
                "audience": r["audience"], "identifier": r["identifier"], "estimated_usd": r["estimated_usd"], "question_id": r["question_id"],
                "anon": r.get("anon"), "feedback": r.get("feedback"), "via": r.get("via"), "prebaked": r.get("prebaked"),
                "model_usd": _model_usd(r)}
    items = [item(r) for r in rows]
    return {
        "period": d["period"], "period_end": d["end"].isoformat(), "selection": d["selection"],
        "new_google": d["new_google"],
        "generated_at": now.isoformat(), "day": start.strftime("%Y-%m-%d"), "yen_per_usd": YEN,
        "user_filter": u, "cost_scope": "all", "people": d["people"], "cost_basis": "hourly_question_share",
        "question_model_cost_basis": "recorded_model_usage",
        "registered_google": d["registered_google"], "active_google": d["active_google"], "unallocated_usd": d["unallocated_usd"],
        "summary": {"questions": d["questions"], "users": d["users"], "first_ms_p50": d["first_p50"],
                    "rejected": d["rejected"], "tools": d["tools"], "cost_usd": d["cost"], "total_usd": d["total"],
                    "feedback": d["feedback"], "pace": d["pace"], "via": d["via"]},
        "hours": {"total": total, "rows": [{"at": h["hour"].isoformat(), "hour": h["hour"].strftime("%H"), "sessions": h["sessions"], "users": h["users"],
                                            "questions": h["questions"], "usd": h["usd"]} for h in hrs]},
        "questions": items,
        "bans": bans(),
        "flagged": [{k: i[k] for k in ("at", "question", "flag", "flag_reason", "anon")}
                    for i in items if i["flag"] and i["flag"] != "normal"],
    }


# ---- ログイン -----------------------------------------------------------------

def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload):
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(_SESSION_KEY, body.encode(), hashlib.sha256).digest())}"


def _verify(value):
    try:
        body, sig = value.split(".")
        if not hmac.compare_digest(sig, _b64(hmac.new(_SESSION_KEY, body.encode(), hashlib.sha256).digest())):
            return None
        payload = json.loads(_unb64(body))
        return payload if payload.get("exp", 0) > time.time() else None
    except (ValueError, KeyError):
        return None


def _cookies(event):
    out = {}
    for c in event.get("cookies") or []:
        k, _, v = c.partition("=")
        out[k.strip()] = v.strip()
    return out


def _cookie(name, value, max_age):
    return f"{name}={value}; Path={COOKIE_PATH}; Max-Age={max_age}; HttpOnly; Secure; SameSite=Lax"


def _base(_event=None):
    return PUBLIC_BASE


def _client():
    return CLIENT_ID


def _redirect(location, cookies=()):
    return {"statusCode": 302, "headers": {"Location": location, "Cache-Control": "no-store"}, "cookies": list(cookies)}


def _login(event):
    """マネージドログインへ送る。state と PKCE の検証値は署名つきの cookie で持つ（10分）"""
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    q = urllib.parse.urlencode({"response_type": "code", "client_id": _client(), "redirect_uri": _base(event) + "callback",
                                "scope": "openid email", "state": state, "code_challenge": challenge,
                                "code_challenge_method": "S256", "lang": "ja"})
    pending = _sign({"state": state, "verifier": verifier, "exp": time.time() + 600})
    return _redirect(f"{AUTH_DOMAIN}/oauth2/authorize?{q}", [_cookie("up_login", pending, 600)])


def _callback(event):
    q = event.get("queryStringParameters") or {}
    pending = _verify(_cookies(event).get("up_login", ""))
    if not pending or not q.get("code") or not hmac.compare_digest(q.get("state", ""), pending["state"]):
        return _html(_message("ログインをやり直してください。", "login"), 400)
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": _client(), "code": q["code"],
                                   "redirect_uri": _base(event) + "callback", "code_verifier": pending["verifier"]}).encode()
    req = urllib.request.Request(f"{AUTH_DOMAIN}/oauth2/token", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            tokens = json.loads(res.read())
    except urllib.error.HTTPError:
        return _html(_message("ログインをやり直してください。", "login"), 400)
    # トークンは Cognito から TLS で直接受け取ったものなので、中身を読んで照合する
    claims = json.loads(_unb64(tokens["id_token"].split(".")[1]))
    issuer = f"https://cognito-idp.{os.environ['AWS_REGION']}.amazonaws.com/{USER_POOL_ID}"
    if (claims.get("iss") != issuer or claims.get("aud") != _client() or not claims.get("email_verified")
            or (claims.get("email") or "").lower() != ADMIN_EMAIL):
        print(json.dumps({"login": "denied"}))
        return _html(_message("このページを見られるのは管理者だけです。", "logout"), 403)
    session = _sign({"email": ADMIN_EMAIL, "exp": time.time() + SESSION_DAYS * 86400})
    print(json.dumps({"login": "ok"}))
    return _redirect(_base(event), [_cookie("up_session", session, SESSION_DAYS * 86400), _cookie("up_login", "", 0)])


def _logout(event):
    q = urllib.parse.urlencode({"client_id": _client(), "logout_uri": _base(event)})
    return _redirect(f"{AUTH_DOMAIN}/logout?{q}", [_cookie("up_session", "", 0)])


def _message(text, link):
    label = "ログイン" if link == "login" else "ログアウト"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>監視ダッシュボード</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2032%2032%22%3E%3Crect%20width%3D%2232%22%20height%3D%2232%22%20rx%3D%227%22%20fill%3D%22%23c55a11%22%2F%3E%3Cpath%20d%3D%22M8%2024V17M16%2024V8M24%2024V12%22%20fill%3D%22none%22%20stroke%3D%22white%22%20stroke-width%3D%224%22%20stroke-linecap%3D%22round%22%2F%3E%3C%2Fsvg%3E"><style>{CSS}</style></head><body><div class="wrap">
<header class="top"><h1><span>みのるんエージェント</span> <span>監視ダッシュボード</span></h1></header><div class="card stat"><p style="margin:0 0 12px">{html.escape(text)}</p>
<a class="btn" href="{link}">{label}</a></div></div></body></html>"""


def _html(body, status=200):
    return {"statusCode": status, "headers": {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                                              "X-Robots-Tag": "noindex"}, "body": body}


def _rolling_cost(now):
    """直近30日間の利用量の積算。1時間単位で集計し、予測月額とは分ける。"""
    start = max(COST_SINCE, (now - datetime.timedelta(days=30)).replace(minute=0, second=0, microsecond=0))
    metrics = _metrics(start, now, 3600)
    rows = _rows(start, now, ["created_at", "tools", "x_search"])
    _, brave, kb = _tool_costs(rows)
    return sum(_cost(metrics, None, brave, kb, x=_x_cost(rows)).values())


def handler(event, _context):
    if "requestContext" not in event:
        # 定期実行：今日の概算コストをメトリクスに出す（EMF）
        d = summary()
        print(json.dumps({"_aws": {"Timestamp": int(d["now"].timestamp() * 1000), "CloudWatchMetrics": [{
            "Namespace": "MinorunAgent", "Dimensions": [[]],
            "Metrics": [{"Name": "EstimatedCostUsd", "Unit": "None"}, {"Name": "QuestionsToday", "Unit": "Count"},
                        {"Name": "UsersToday", "Unit": "Count"}]}]},
            "EstimatedCostUsd": round(d["total"], 4), "QuestionsToday": d["questions"], "UsersToday": d["users"]}))
        rolling = _rolling_cost(d["now"])
        print(json.dumps({"_aws": {"Timestamp": int(d["now"].timestamp() * 1000), "CloudWatchMetrics": [{
            "Namespace": "MinorunAgent", "Dimensions": [[]],
            "Metrics": [{"Name": "Rolling30DayCostUsd", "Unit": "None"}]}]},
            "Rolling30DayCostUsd": round(rolling, 4)}))
        return {"total": d["total"], "rolling_30d_usd": rolling}
    # CloudFront からは /admin/... のまま届く。関数URLを直接開いたときは公開URLへ送る
    path = event.get("rawPath") or "/"
    prefix = COOKIE_PATH if COOKIE_PATH != "/" else ""
    if prefix and not path.startswith(prefix):
        if (event.get("queryStringParameters") or {}).get("format") != "json":
            return _redirect(PUBLIC_BASE)
    elif prefix:
        path = path[len(prefix):]
        if not path:
            return _redirect(PUBLIC_BASE)
    path = path or "/"
    if path == "/login":
        return _login(event)
    if path == "/callback":
        return _callback(event)
    if path == "/logout":
        return _logout(event)
    q = event.get("queryStringParameters") or {}
    view, day = q.get("v"), q.get("d")
    if q.get("format") == "json":
        if not hmac.compare_digest(q.get("k", ""), TOKEN):
            return {"statusCode": 403, "headers": {"Content-Type": "text/plain; charset=utf-8"}, "body": "forbidden"}
        return {"statusCode": 200, "headers": {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
                "body": json.dumps(as_json(day, q.get("u") or "all"), ensure_ascii=False, default=str)}
    session = _verify(_cookies(event).get("up_session", ""))
    if not session or session.get("email") != ADMIN_EMAIL:
        return _redirect(_base(event) + "login")
    if path == "/ban-reviewed":
        if event.get("requestContext", {}).get("http", {}).get("method") != "POST":
            return _html("method not allowed", 405)
        return _review_ban(event)
    if path == "/owner":
        if event.get("requestContext", {}).get("http", {}).get("method") != "POST":
            return _html("method not allowed", 405)
        return _register_owner(event)
    try:
        page = int(q.get("p", "1"))
    except ValueError:
        page = 1
    if view == "bans":
        return _html(render_bans(q.get("u"), page))
    return _html(render(view, day, page, q.get("f"), q.get("u"), q.get("q"), q.get("g", "registered")))

CSS += """
.cost .monthly{grid-column:1/-1;text-align:center;background:#f3f4f6;border-radius:8px;padding:12px 8px;margin-top:4px}
.ban-wrap{max-width:1000px}.ban-nav{display:flex;gap:24px;margin-bottom:24px;font-size:14px}
.ban-person{display:block;padding:22px 0;border-bottom:1px solid var(--line);color:var(--ink)}.ban-person:hover{background:var(--soft)}
.ban-person-title{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}.ban-person p{font-size:14px;margin:8px 0}
.ban-wrap .alert{color:var(--warn);font-weight:600}.ban-id{padding:0;font-size:20px;overflow-wrap:anywhere}
.ban-visits{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:24px;padding:18px 0;border-block:1px solid var(--line)}
.ban-visits span{display:block;font-size:13px;color:var(--sub)}.ban-visits strong{display:block;font-size:34px;font-weight:600}.ban-visits small{margin-left:6px}
.ban-cause{background:var(--soft);border-radius:10px;padding:18px;margin:24px 0}.ban-cause h2{padding:0}.ban-cause p{overflow-wrap:anywhere}.ban-wrap .meta{font-size:12px}
.ban-wrap .list{max-height:none;overflow:visible;padding:0}.ban-wrap section>h2{padding-left:0}.ban-cause .ans{background:#fff}
"""

CSS += """
.error-card{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 20px;margin-bottom:20px;color:var(--ink);flex-wrap:wrap}
.error-card strong{font-size:28px}.error-card small{font-size:14px}.error-card .warn,#errors-panel .warn{color:var(--warn)}
#errors-panel .list{max-height:none;overflow:visible}#errors-panel .ans{overflow-wrap:anywhere}
"""

CSS += """
header.dashboard-top .day{display:grid;grid-template-columns:auto minmax(0,1fr);width:100%;min-width:0;align-items:center;gap:12px 24px}
.period-units{display:inline-flex;background:#eef1f5;border-radius:10px;padding:3px;gap:2px;justify-self:start}
.period-units a{min-width:64px;min-height:38px;display:flex;align-items:center;justify-content:center;padding:0 14px;border-radius:8px;font-size:14px;font-weight:600;color:var(--sub)}
.period-units a:hover{color:var(--ink)}
.period-units a[aria-current]{background:#fff;color:var(--ink);box-shadow:0 1px 2px rgba(16,24,40,.12)}
.period-nav{display:flex;align-items:center;justify-content:flex-end;gap:4px;min-width:0}
.period-nav b{font-size:15px;font-weight:600;text-align:center;font-variant-numeric:tabular-nums;padding:0 6px}
.period-nav .step{display:flex;align-items:center;justify-content:center;width:38px;height:38px;border-radius:8px;font-size:22px;line-height:1;color:var(--ink)}
.period-nav a.step:hover{background:#eef1f5}.period-nav .step.off{color:#d0d5dd}.period-nav.loading{opacity:.35;transition:opacity .15s}
.day .date-tabs{grid-column:1/-1;max-width:100%;margin:0}
.people-pair{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.people-pair>a{display:flex;flex-direction:column;align-items:flex-start;min-width:0;color:var(--ink);text-decoration:none;border-radius:6px}
.people-pair>a:hover{background:#edf2f7}.people-pair span{white-space:normal}.people-pair strong{line-height:36px}
.people-panel{grid-template-columns:minmax(0,1.5fr) minmax(0,1fr) minmax(0,1fr)}
.user-directory{padding:20px}.user-directory nav{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.user-directory [aria-current=page]{color:var(--accent);font-weight:600}
.user-row{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:18px 0;border-bottom:1px solid var(--line);font-size:14px;overflow-wrap:anywhere}
.user-row span{color:var(--sub)}
@media(max-width:1050px){.people-panel{grid-template-columns:1fr 1fr}.people-stats{grid-column:1/-1}.people-owner{grid-column:auto;display:block}.people-owner button{width:100%}}
@media(max-width:899px){header.dashboard-top .day{grid-template-columns:1fr;gap:10px}.period-units{display:grid;grid-template-columns:repeat(4,1fr);width:100%}.period-units a{min-width:0;padding:0 4px}.period-nav{justify-content:space-between}.period-nav b{flex:1;min-width:0;font-size:13px}}
@media(max-width:699px){.people-panel{grid-template-columns:1fr}.people-pair{gap:10px}.user-directory{padding:14px}.user-row{display:grid}}
"""
