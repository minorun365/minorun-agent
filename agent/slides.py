"""スライドの依頼を受け付ける（ツール make_slides の中身）。記事のURLから作るものと、調べた要点と出典から作るものの2種類。

回数を表で数え（1人1日・全体で1日）、進み具合の最初の1行を公開用 S3 に置き、スライド工房（AgentCore Runtime）をジョブごとのセッションで起こす。
ログインしていない人も頼める（2026-11-21 みのるん決定）。匿名IDで1日1本・同じIPから1日3本まで、ログインした人は1日2本。
匿名で作った分は匿名IDの一覧に残し、ログインしたら claim でその人の一覧へ移す。
"""

import datetime
import ipaddress
import json
import secrets
import threading
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


def _today():
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def _count(table, limits):
    """limits は [(回数の鍵, 上限), …]。全部を同時に1つ増やす。どれかが上限なら増やさずに、上限に当たった鍵の位置を返す。"""
    expires = int(time.time()) + 3 * 86400
    def item(pk, limit):
        return {"Update": {"TableName": table, "Key": {"pk": {"S": pk}},
                           "UpdateExpression": "ADD n :one SET expires = :exp",
                           "ConditionExpression": "attribute_not_exists(n) OR n < :lim",
                           "ExpressionAttributeValues": {":one": {"N": "1"}, ":lim": {"N": str(limit)},
                                                         ":exp": {"N": str(expires)}}}}
    try:
        _client("dynamodb").transact_write_items(TransactItems=[item(pk, lim) for pk, lim in limits])
        return None
    except _client("dynamodb").exceptions.TransactionCanceledException as e:
        reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
        for i, code in enumerate(reasons):
            if code == "ConditionalCheckFailed":
                return i
        raise


MAX_SOURCES = 5
NOTES_MIN, NOTES_MAX = 300, 8000


def topic_material(topic, notes, sources):
    """調べてまとめるときの材料を整える。足りなければ None。出典は公開URLだけを重複なしで5件まで"""
    topic = " ".join(str(topic or "").split())[:100]
    notes = str(notes or "").strip()[:NOTES_MAX]
    urls = []
    for u in sources or []:
        u = str(u).strip()
        if valid_url(u) and u not in urls:
            urls.append(u)
    if not topic or len(notes) < NOTES_MIN:
        return None
    return {"kind": "topic", "topic": topic, "notes": notes, "sources": urls[:MAX_SOURCES]}


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _launch(runtime, body, table, bucket):
    """工房の Runtime を起こす。セッション ID はジョブごとに変える（1本＝1つの入れ物。33字以上が決まり）。
    工房は受け取ってすぐ返し、裏で作る。起こせなかったら、画面へ失敗を出し、数えた回数を戻す。"""
    job = body["job"]
    try:
        _client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=runtime, qualifier="DEFAULT", runtimeSessionId=f"slides-job-{job}-{secrets.token_hex(8)}",
            contentType="application/json", accept="application/json",
            payload=json.dumps(body, ensure_ascii=False).encode())["response"].read()
    except Exception as e:
        print(json.dumps({"slides_launch_error": type(e).__name__, "job": job, "detail": str(e)[:300]}))
        try:
            _client("s3").put_object(Bucket=bucket, Key=f"s/{job}/status.json", ContentType="application/json",
                                     CacheControl="no-cache, max-age=0",
                                     Body=json.dumps({"stage": "error", "at": int(time.time()),
                                                      "message": "スライド工房を起こせませんでした。今日の回数には数えないので、少し時間をおいて、もう一度頼んでください。"},
                                                     ensure_ascii=False).encode())
            for pk in body.get("counted") or []:
                _client("dynamodb").update_item(TableName=table, Key={"pk": {"S": pk}}, UpdateExpression="ADD n :minus",
                                                ConditionExpression="n > :zero",
                                                ExpressionAttributeValues={":minus": {"N": "-1"}, ":zero": {"N": "0"}})
        except Exception as e2:
            print(json.dumps({"slides_launch_cleanup_error": type(e2).__name__, "job": job}))


GUEST_NOTE = "Googleでログインすると、1日2本まで作れます"


def owner(user, anon):
    """作ったスライドの一覧の持ち主。ログインした人はそのハッシュ、していない人は匿名IDのハッシュ。"""
    return user if user else (f"a:{anon}" if anon else "")


def start(material, request, user, *, runtime, table, bucket, user_daily, total_daily, unlimited=False,
          anon="", ip="", guest_daily=1, ip_daily=3):
    """material は記事のURL（文字列）か、topic_material の結果（調べてまとめる）。
    回数はどちらも同じ枠で数える。unlimited は運営者のテスト用（ログインした人だけ）。1人の上限にも全体の上限にも数えない。
    ログインしていない人は匿名ID（anon）と送信元IPのハッシュ（ip）で数える。"""
    if not runtime:
        return {"status": "unavailable", "note": "この環境ではスライド作成を使えません"}
    if not (user or anon):
        return {"status": "unavailable", "note": "画面を読み込み直してから、もう一度頼んでください"}
    if isinstance(material, str):
        url = material.strip()
        if not valid_url(url):
            return {"status": "invalid", "note": "公開されているページの http(s) のURLが必要です"}
        payload, kind = {"url": url}, "article"
    elif material:
        payload, kind = material, "topic"
    else:
        return {"status": "invalid", "note": "スライドにする材料が足りません。先に調べた要点（300字以上）を渡してください"}
    day = _today()
    if user:
        limits = [(f"user#{user}#{day}", user_daily)]
    else:
        limits = [(f"guest#{anon}#{day}", guest_daily)] + ([(f"ip#{ip}#{day}", ip_daily)] if ip else [])
    limits.append((f"all#{day}", total_daily))
    counting = not (user and unlimited)
    hit = _count(table, limits) if counting else None
    if hit is not None:
        if hit == len(limits) - 1:
            return {"status": "busy", "note": "今日は全体の上限に達しました。明日また使えます"}
        if user:
            return {"status": "limit", "note": f"今日の分（1人1日{user_daily}本）を使い切りました。明日（日本時間の0時以降）にまた使えます"}
        return {"status": "guest_limit",
                "note": f"ログインせずに作れる今日の分（1日{guest_daily}本）を使い切りました。{GUEST_NOTE}"}
    job = secrets.token_urlsafe(12).replace("-", "a").replace("_", "b")
    _client("s3").put_object(Bucket=bucket, Key=f"s/{job}/status.json", ContentType="application/json",
                             CacheControl="no-cache, max-age=0",
                             Body=json.dumps({"stage": "queued", "kind": kind, "at": int(time.time())}).encode())
    who = owner(user, anon)
    body = {"job": job, **payload, "ask": str(request)[:300], "user": who,
            # 数えた回数の鍵。工房が作れなかったときに、これを1つずつ戻す
            **({"counted": [pk for pk, _ in limits]} if counting else {})}
    # 工房の入れ物の立ち上げに20秒ほどかかるので、呼び出しは裏で行い、回答を待たせない
    _spawn(_launch, runtime, body, table, bucket)
    _remember(table, who, job)
    print(json.dumps({"slides_job": job, "kind": kind, "user": who, **({"unlimited": True} if not counting else {})}))
    if user:
        return {"status": "accepted", "job": job, "note": "数分ででき上がります。画面を離れても、右上のアイコンの「作ったスライド」から開けます"}
    return {"status": "accepted", "job": job,
            "note": "数分ででき上がります。この画面で待つか、あとで同じブラウザで開き直すと右上のアイコンの「作ったスライド」から開けます。"
                    "待つ間にGoogleでログインしておくと、どの端末からでも開けます"}


OFFER_MIN_CHARS = 250
# 札を出さない検索：URLを読んだだけ（URLの札がある）と、セッションの日時・場所を引いただけ（一問一答）
OFFER_SKIP = frozenset({"fetch_url", "search_sessions"})


def offer(answer, tools_used, search_tools):
    """回答のあとに「この回答をスライドに」の札を出すか。ナレッジベースやWebで調べた、説明として組める長さの回答だけ"""
    used = set(tools_used) & set(search_tools)
    return bool(used - OFFER_SKIP) and len((answer or "").strip()) >= OFFER_MIN_CHARS


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


def claim(table, anon, user):
    """匿名で作ったスライドを、ログインした人の一覧へ移す（同じブラウザでログインしたとき）。移した件数を返す。"""
    if not (table and anon and user):
        return 0
    key = {"pk": {"S": f"list#a:{anon}"}}
    jobs = _client("dynamodb").get_item(TableName=table, Key=key).get("Item", {}).get("jobs", {}).get("L", [])
    if not jobs:
        return 0
    _client("dynamodb").update_item(
        TableName=table, Key={"pk": {"S": f"list#{user}"}},
        UpdateExpression="SET jobs = list_append(if_not_exists(jobs, :empty), :new)",
        ExpressionAttributeValues={":empty": {"L": []}, ":new": {"L": jobs}})
    _client("dynamodb").update_item(TableName=table, Key=key, UpdateExpression="REMOVE jobs")
    print(json.dumps({"slides_claim": len(jobs), "user": user}))
    return len(jobs)


def recent(table, user):
    """その人が作ったスライドを新しい順に最大 KEEP 件返す。"""
    if not (table and user):
        return []
    item = _client("dynamodb").get_item(TableName=table, Key={"pk": {"S": f"list#{user}"}}).get("Item") or {}
    jobs = {j["M"]["job"]["S"]: {"job": j["M"]["job"]["S"], "at": int(j["M"]["at"]["N"])} for j in item.get("jobs", {}).get("L", [])}
    return sorted(jobs.values(), key=lambda j: -j["at"])[:KEEP]
