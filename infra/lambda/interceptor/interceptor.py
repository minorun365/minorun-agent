"""入口の Gateway の Lambda インターセプター（REQUEST）。

匿名IDの形・文字数・停止スイッチを見て、DynamoDB で1分と今日の件数を1つずつ足す。
Google でログインした人（x-access-token に Cognito のアクセストークン）は上限を緩め、利用者のハッシュを Runtime へ渡す。
上限を超えていれば Runtime へ渡さず、その場で断りの返事を返す。
日の区切りは日本時間の0時（利用者の大半は日本から使う）。
"""

import base64
import datetime
import time
import hashlib
import json
import os
import re

import boto3
from boto3.dynamodb.conditions import Attr, Key

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "30"))
MAX_CHARS = int(os.environ.get("MAX_CHARS", "1000"))
PER_MINUTE = int(os.environ.get("PER_MINUTE", "5"))
USER_DAILY_LIMIT = int(os.environ.get("USER_DAILY_LIMIT", "100"))
USER_PER_MINUTE = int(os.environ.get("USER_PER_MINUTE", "10"))
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
USER_CLIENT_ID = os.environ.get("USER_CLIENT_ID", "")
COGNITO = boto3.client("cognito-idp")
# 回答の評価（良い・良くない）は会話の表の該当する1行へ書き足す。表の名前が無ければ評価は受け付けない
CONVERSATIONS = boto3.resource("dynamodb").Table(os.environ["CONVERSATIONS_TABLE"]) if os.environ.get("CONVERSATIONS_TABLE") else None
TURN_ID = re.compile(r"^[A-Za-z0-9]{8,40}$")
SESSION_ID = re.compile(r"^[A-Za-z0-9-]{16,100}$")
_users = {}  # トークンのハッシュ → (利用者のハッシュ, 期限)。同じトークンで毎回 Cognito を呼ばないよう、入れ物の中だけで覚える
JST = datetime.timezone(datetime.timedelta(hours=9))
ANON_ID = re.compile(r"^[A-Za-z0-9-]{16,64}$")

MESSAGES = {
    "bad_id": "画面を読み込み直してから、もう一度送ってください。",
    "too_long": f"質問は{MAX_CHARS}字までにしてください。",
    "stopped": "いま混み合っています。少し時間をおいて試してください。",
    "per_minute": "少し間をあけてから、もう一度送ってください。",
    "daily_limit": "今日の質問はここまで。日本時間の0時に戻ります。",
    "banned": "良識の範囲内での利用を逸脱する行為が確認されました。今後このアプリの利用をお断りします。",
}


def _banned(anon_hash, user):
    """利用停止の表（回数の表の pk=ban#a#匿名IDのハッシュ / ban#u#利用者のハッシュ）に載っていれば、載っていた行の pk を返す。
    片方だけ載っているときは、もう片方も載せる（停止された匿名IDでログインした・停止された人が別の端末から来た）。"""
    keys = [f"ban#a#{anon_hash}"] + ([f"ban#u#{user}"] if user else [])
    found = TABLE.meta.client.batch_get_item(
        RequestItems={TABLE.name: {"Keys": [{"pk": k} for k in keys], "ProjectionExpression": "pk"}}
    )["Responses"].get(TABLE.name, [])
    hit = {i["pk"] for i in found}
    if not hit:
        return None
    for k in set(keys) - hit:
        TABLE.put_item(Item={"pk": k, "source": "linked", "linked_from": sorted(hit)[0],
                             "at": datetime.datetime.now(JST).isoformat(timespec="seconds")})
    return sorted(hit)[0]  # 匿名IDの行を優先（同じ人を2行で二重に数えない）


def _seen(pk, opened):
    """停止された人が来た回数を停止の行へ足す。opened はページを開いた（先読みで告知が出た）とき、
    それ以外は質問・評価を送ろうとしたとき。数えるのに失敗しても断ることは変えない"""
    name = "views" if opened else "attempts"
    try:
        # views は DynamoDB の予約語なので、名前は置き換えて渡す（直書きすると ValidationException）
        TABLE.update_item(Key={"pk": pk}, UpdateExpression="ADD #n :one SET seen_at = :t", ExpressionAttributeNames={"#n": name},
                          ExpressionAttributeValues={":one": 1, ":t": datetime.datetime.now(JST).isoformat(timespec="seconds")})
    except Exception as e:
        print(json.dumps({"ban_seen_error": type(e).__name__}))


def _section(event):
    # MCP の Gateway では event["mcp"]、それ以外の Gateway では別のキーに入る。
    # 中に gatewayRequest を持つキーを探して、返事も同じキーで返す。
    for key, value in event.items():
        if isinstance(value, dict) and "gatewayRequest" in value:
            return key, value["gatewayRequest"]
    raise ValueError(f"gatewayRequest が見つからない: {list(event)}")


def _header(request, name):
    headers = request.get("headers") or {}
    for k, v in headers.items():
        if k.lower() == name:
            return v[0] if isinstance(v, list) else v
    return None


def _body(request):
    # HTTP の Gateway では本文が base64 の文字列で届く（MCP の Gateway は JSON のまま）
    body = request.get("body")
    if isinstance(body, str):
        for text in (body, _b64(body)):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                pass
        return {}
    return body or {}


def _b64(text):
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _count(pk, expires_at):
    result = TABLE.update_item(
        Key={"pk": pk},
        UpdateExpression="ADD #c :one SET expires = :exp",
        ExpressionAttributeNames={"#c": "count"},
        ExpressionAttributeValues={":one": 1, ":exp": int(expires_at.timestamp())},
        ReturnValues="UPDATED_NEW",
    )
    return int(result["Attributes"]["count"])


def _anon(anon_id):
    # 匿名IDのハッシュ（16桁）。Runtime へ渡す値と同じなので、判定のログと会話の記録を突き合わせられる
    return hashlib.sha256(anon_id.encode()).hexdigest()[:16]


def _claims(token):
    # 署名の確認は Cognito の GetUser に任せる。ここでは別のプール・別のアプリのトークンを弾くために中身だけ読む
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def _user(token):
    """ログインしていれば利用者のハッシュ（16桁）を返す。トークンが無い・無効なら None（匿名として扱う）。"""
    if not token or not USER_POOL_ID:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    hit = _users.get(key)
    if hit and hit[1] > now:
        return hit[0]
    c = _claims(token)
    region = USER_POOL_ID.split("_")[0]
    if c.get("iss") != f"https://cognito-idp.{region}.amazonaws.com/{USER_POOL_ID}" or c.get("client_id") != USER_CLIENT_ID \
            or c.get("token_use") != "access" or c.get("exp", 0) < now:
        return None
    try:
        sub = COGNITO.get_user(AccessToken=token)["Username"]
    except Exception:
        return None
    user = hashlib.sha256(f"user:{sub}".encode()).hexdigest()[:16]
    _users[key] = (user, min(c["exp"], now + 600))
    return user


def _reject(key, reason, status, anon_id=None):
    # 誰が上限に当たったかを数えられるよう、匿名IDのハッシュを添える（IP などは書かない。bad_id のときは無し）
    print(json.dumps({"decision": "reject", "reason": reason, **({"anon": _anon(anon_id)} if anon_id else {})}))
    # HTTP の Gateway では本文を base64 の文字列で返す（JSON のまま返すと
    # 「Received invalid response from interceptor」で 400 になる。2026-10-25 実測）
    body = json.dumps({"error": reason, "message": MESSAGES[reason]}, ensure_ascii=False)
    return {
        "interceptorOutputVersion": "1.0",
        key: {
            "transformedGatewayResponse": {
                "statusCode": status,
                "contentType": "application/json",
                "body": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            }
        },
    }


def handler(event, context):
    # 受け取った中身は丸ごと書かない（X-Forwarded-For の IP がログに180日残る）
    key, request = _section(event)

    anon_id = _header(request, "x-anon-id")
    if not anon_id or not ANON_ID.match(anon_id):
        return _reject(key, "bad_id", 400)

    body = _body(request)
    prompt = str(body.get("prompt", ""))
    if len(prompt) > MAX_CHARS:
        return _reject(key, "too_long", 400, anon_id)

    if TABLE.get_item(Key={"pk": "switch"}).get("Item", {}).get("stopped"):
        return _reject(key, "stopped", 503, anon_id)

    now = datetime.datetime.now(JST)
    user = _user(_header(request, "x-access-token"))
    # 利用停止は評価・先読みを含むすべての要求で断る（ページを開いた時点の先読みで画面に告知が出る）
    hit = _banned(_anon(anon_id), user)
    if hit:
        _seen(hit, body.get("warmup") is True)
        return _reject(key, "banned", 403, anon_id)
    if "feedback" in body:
        # 回答の評価。モデルは呼ばず、ここで会話の表に書いて返す（1日の件数には数えず、連打だけ止める）
        if _count(f"fb#{now:%Y-%m-%dT%H:%M}#{anon_id}", now + datetime.timedelta(minutes=2)) > 20:
            return _reject(key, "per_minute", 429, anon_id)
        return _feedback(key, request, body, anon_id, user)
    if body.get("warmup") is True:
        # ページを開いたときの先読み（会話の入れ物を先に起こす）。モデルを呼ばないので1日の件数には数えず、連打だけ止める
        if _count(f"warm#{now:%Y-%m-%dT%H:%M}#{anon_id}", now + datetime.timedelta(minutes=2)) > 3:
            return _reject(key, "per_minute", 429, anon_id)
        return _pass(key, {"warmup": True}, anon_id, user)
    # 連打は1分単位で止める。認証なしの Gateway は匿名IDごとの回数制限を持てないので、ここで数える。
    # ログインした人は端末をまたいで同じ人として数え、上限を緩める
    who = f"u:{user}" if user else anon_id
    per_minute, daily = (USER_PER_MINUTE, USER_DAILY_LIMIT) if user else (PER_MINUTE, DAILY_LIMIT)
    if _count(f"min#{now:%Y-%m-%dT%H:%M}#{who}", now + datetime.timedelta(minutes=2)) > per_minute:
        return _reject(key, "per_minute", 429, anon_id)
    tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    count = _count(f"day#{now:%Y-%m-%d}#{who}", tomorrow + datetime.timedelta(hours=1))
    if count > daily:
        return _reject(key, "daily_limit", 429, anon_id)

    print(json.dumps({"decision": "pass", "count": count, "anon": _anon(anon_id), **({"user": user} if user else {})}))
    passed = {"prompt": prompt}
    tid = str(body.get("tid", ""))
    if TURN_ID.match(tid):
        passed["tid"] = tid  # 画面が質問ごとに作る番号。後から届く評価を、この質問の行へ結び付ける
    if body.get("via") in ("typed", "ai", "starter", "link"):
        passed["via"] = body["via"]  # 質問の出どころ（自由入力・AI の候補・用意した質問・?ask= のリンク）
    return _pass(key, passed, anon_id, user)


def _respond(key, status, payload):
    body = json.dumps(payload, ensure_ascii=False)
    return {
        "interceptorOutputVersion": "1.0",
        key: {"transformedGatewayResponse": {"statusCode": status, "contentType": "application/json",
                                             "body": base64.b64encode(body.encode("utf-8")).decode("ascii")}},
    }


def _feedback(key, request, body, anon_id, user=None):
    value, tid = body.get("feedback"), str(body.get("tid", ""))
    session = _header(request, "x-amzn-bedrock-agentcore-runtime-session-id") or ""
    if CONVERSATIONS is None or value not in ("good", "bad", "none") or not TURN_ID.match(tid) or not SESSION_ID.match(session):
        return _respond(key, 400, {"ok": False})
    # 記録は回答を返し終えた後に裏で書かれるので、すぐ押されたときは少し待って探し直す
    item = None
    for wait in (0, 1, 1.5):
        time.sleep(wait)
        found = CONVERSATIONS.query(KeyConditionExpression=Key("pk").eq(session), FilterExpression=Attr("tid").eq(tid),
                                    ProjectionExpression="pk, sk, anon")["Items"]
        if found:
            item = found[0]
            break
    # 別の人の会話の行は書き換えない（会話IDを他人に知られても、評価を付け替えられないように）
    owners = {_anon(anon_id)} | ({f"user-{user}"} if user else set())
    if not item or item.get("anon") not in owners:
        print(json.dumps({"decision": "feedback", "result": "not_found", "anon": _anon(anon_id)}))
        return _respond(key, 404, {"ok": False})
    k = {"pk": item["pk"], "sk": item["sk"]}
    if value == "none":
        CONVERSATIONS.update_item(Key=k, UpdateExpression="REMOVE feedback, feedback_at")
    else:
        CONVERSATIONS.update_item(Key=k, UpdateExpression="SET feedback = :v, feedback_at = :t",
                                  ExpressionAttributeValues={":v": value, ":t": datetime.datetime.now(JST).isoformat(timespec="seconds")})
    print(json.dumps({"decision": "feedback", "result": value, "anon": _anon(anon_id)}))
    return _respond(key, 200, {"ok": True})


def _pass(key, body, anon_id, user=None):
    # Runtime へは独自のヘッダーが届かないので、匿名IDのハッシュを本文に入れて渡す（会話の記録とトレースの手がかり）。
    # 画面から送られた余計な項目は通さず、決まった項目だけで本文を作り直す
    body["anon"] = _anon(anon_id)
    if user:
        body["user"] = user
    encoded = base64.b64encode(json.dumps(body, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return {"interceptorOutputVersion": "1.0", key: {"transformedGatewayRequest": {"body": encoded}}}
