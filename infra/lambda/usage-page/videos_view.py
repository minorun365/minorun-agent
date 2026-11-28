"""利用者が資料から作った解説動画を、監視ダッシュボードの「動画」タブにポスターつきで並べる（スライドのタブと同じ形）。

正本は3か所に分かれている。
  - 表 minorun-agent-videos の list#<持ち主> … 番号と受け付けた時刻。持ち主はログインした人なら利用者、していない人なら「a:匿名ID」
  - 動画の S3 の v/<番号>/status.json … 進み具合・題・長さ・使ったページ数。できたものは meta.json に資料の名前
  - 動画作成機能（Runtime minorun_agent_video）のログの最後の1行 … トークン数・かかった秒数・失敗の理由
動画は画面の添付ボタンから作るので、スライドと違って頼んだ質問は無い。代わりに資料の名前を出す。
"""
import concurrent.futures
import datetime
import html
import json
import os

import boto3

import slides_view

TABLE = os.environ.get("VIDEOS_TABLE", "")
BUCKET = os.environ.get("VIDEOS_BUCKET", "")
# ロググループの名前に Runtime の ID が入るので、前方一致で探す（MinorunVideo を参照するとスタックの出し直しが連鎖するため）
LOG_PREFIX = os.environ.get("VIDEOS_LOG_PREFIX", "")
JST = slides_view.JST
STAGES = {"queued": "順番待ち", "reading": "資料を読んでいる", "writing": "台本を書いている",
          "voicing": "読み上げを録っている", "rendering": "動画にまとめている"}
# 動画作成の Runtime は1本30分で打ち切る（video-stack.ts の maxLifetime）。それより長く途中のままなのは落ちたもの
STALE = 30 * 60
# 1本ごとの Opus のトークン（Claude API・Bedrock のどちらでも同じ単価。100万トークンあたりのドル）
PRICE = slides_view.API_PRICE
_groups = None


def _log_groups():
    global _groups
    if _groups is None:
        try:
            page = boto3.client("logs").describe_log_groups(logGroupNamePrefix=LOG_PREFIX)
            _groups = [g["logGroupName"] for g in page.get("logGroups", [])]
        except Exception as e:
            print(json.dumps({"videos_logs": "unavailable", "error": type(e).__name__}))
            return []
    return _groups


def _jobs(start, end):
    ddb = boto3.client("dynamodb")
    args = {"TableName": TABLE, "FilterExpression": "begins_with(pk, :p)", "ExpressionAttributeValues": {":p": {"S": "list#"}}}
    out = []
    while True:
        page = ddb.scan(**args)
        for item in page["Items"]:
            owner = item["pk"]["S"][5:]
            for j in item.get("jobs", {}).get("L", []):
                at = int(j["M"]["at"]["N"])
                if start.timestamp() <= at < end.timestamp():
                    out.append({"job": j["M"]["job"]["S"], "at": at, "user": owner})
        if not page.get("LastEvaluatedKey"):
            return sorted(out, key=lambda j: -j["at"])
        args["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _read(job, name):
    try:
        return json.loads(boto3.client("s3").get_object(Bucket=BUCKET, Key=f"v/{job}/{name}")["Body"].read())
    except Exception:
        return None


def _status(job):
    st = _read(job, "status.json") or {"stage": "unknown"}
    if st.get("stage") == "done" and not st.get("name"):
        st["name"] = (_read(job, "meta.json") or {}).get("name") or ""
    return st


def _logs(start, end):
    start_ms = int(start.timestamp() * 1000)
    end_ms = int((end + datetime.timedelta(minutes=STALE // 60)).timestamp() * 1000)
    now_ms = int(datetime.datetime.now(JST).timestamp() * 1000)
    out = {}
    for group in _log_groups():
        for _, rec in slides_view._group(group, start_ms, end_ms, now_ms):
            out[rec.get("job")] = rec
    return out


def usd(log):
    u = log.get("usage")
    if not isinstance(u, dict):
        return None
    return (u.get("input", 0) * PRICE["input"] + u.get("output", 0) * PRICE["output"]) / 1e6


def _events(logs, group, pattern, start_ms, end_ms):
    args = {"logGroupName": group, "filterPattern": pattern, "startTime": start_ms, "endTime": end_ms}
    out = []
    while True:
        page = logs.filter_log_events(**args)
        for e in page.get("events", []):
            try:
                out.append((e["timestamp"], e.get("logStreamName", ""), json.loads(e["message"][e["message"].index("{"):])))
            except ValueError:
                continue
        token = page.get("nextToken")
        if not isinstance(token, str) or not token or token == args.get("nextToken"):
            return out
        args["nextToken"] = token


def api_usd_by_hour(start, end):
    """動画作成機能が Claude API（Anthropic のクレジット）で払った Opus の費用を、日本時間の時台ごとに返す。
    AWS/Bedrock のメトリクスには載らないので、1本ごとの最後のログ行のトークン数から足す。
    Claude API が使えず Bedrock へ回った行（model_route）が同じログストリーム＝同じジョブにあるものは、
    Bedrock の Opus のトークン数として既に数えているので足さない（二重に数えない）。"""
    if not LOG_PREFIX:
        return {}
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    out = {}
    try:
        logs = boto3.client("logs")
        for group in _log_groups():
            fell = {stream for _, stream, _ in _events(logs, group, "{ $.model_route = * }",
                                                       start_ms - STALE * 1000, end_ms)}
            for ts, stream, rec in _events(logs, group, "{ $.job = * && $.ok IS TRUE }", start_ms, end_ms):
                if stream in fell:
                    continue
                h = datetime.datetime.fromtimestamp(ts / 1000, JST).replace(minute=0, second=0, microsecond=0)
                out[h] = out.get(h, 0.0) + (usd(rec) or 0.0)
    except Exception as e:
        print(json.dumps({"videos_cost": "unavailable", "error": type(e).__name__}))
    return out


def load(start, end):
    """選んだ期間に受け付けた動画。取れなければ None（0本と表示しない）。"""
    if not (TABLE and BUCKET):
        return None
    try:
        jobs = _jobs(start, end)
    except Exception:
        print(json.dumps({"videos_table": "unavailable"}))
        return None
    if not jobs:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        log_future = pool.submit(_logs, start, end)
        statuses = list(pool.map(_status, [j["job"] for j in jobs]))
        logs = log_future.result()
    for j, st in zip(jobs, statuses):
        j["status"] = st
        j["log"] = logs.get(j["job"], {})
    return jobs


def row(owner):
    """持ち主を、質問の行と同じ形（user か anon）にする。表示対象の振り分けと利用者IDの表示に使う。"""
    return {"anon": owner[2:], "pk": ""} if owner.startswith("a:") else {"user": owner, "pk": ""}


def identifier(owner):
    return ("A-" + owner[2:]) if owner.startswith("a:") else ("G-" + owner)


def state(j, now):
    st = j["status"]
    stage = st.get("stage", "unknown")
    if stage in ("done", "error"):
        return stage
    if stage in STAGES and now.timestamp() - st.get("at", j["at"]) > STALE:
        return "error"
    return "working" if stage in STAGES else "unknown"


def _length(seconds):
    s = round(seconds)
    return f"{s // 60}分{s % 60:02d}秒"


def card(j, now, multi, url):
    esc = html.escape
    st, log = j["status"], j["log"]
    kind = state(j, now)
    base = f"/v/{j['job']}/"
    if kind == "done":
        thumb = (f'<a class="slide-thumb" href="{esc(base)}" target="_blank" rel="noopener">'
                 f'<img src="{esc(base)}poster.jpg" alt="" loading="lazy" width="1280" height="720"></a>')
        title = esc(st.get("title") or "（題なし）")
    elif kind == "error":
        reason = st.get("message") or "途中で止まり、結果を書けませんでした。"
        thumb = '<div class="slide-thumb empty bad"><span>作れなかった</span></div>'
        title = f'<span class="warn">{esc(reason)}</span>'
    else:
        thumb = f'<div class="slide-thumb empty"><span>{esc(STAGES.get(st.get("stage"), "状態不明"))}…</span></div>'
        title = "作成中"
    who = identifier(j["user"])
    meta = [slides_view._when(j["at"], multi), f'<a href="{esc(url(v="list", d=j["day"], u=who))}">{esc(who)}</a>']
    if st.get("seconds"):
        meta.append(_length(st["seconds"]))
    if st.get("pages"):
        total = st.get("total") or st["pages"]
        meta.append(f'{int(st["pages"])}ページ' + (f'（全{int(total)}ページの先頭）' if total > st["pages"] else ""))
    if log.get("elapsed"):
        meta.append(f'作成{round(log["elapsed"] / 60)}分')
    cost = usd(log)
    if cost is not None:
        meta.append(f'${cost:.2f}')
    if kind == "error" and log.get("error"):
        meta.append(esc(log["error"]))
    name = st.get("name") or ""
    doc = (f'<p class="slide-ask">資料：{esc(name)}</p>' if name else "")
    open_link = f'<a class="slide-open" href="{esc(base)}" target="_blank" rel="noopener">動画のページを開く ›</a>' if kind == "done" else ""
    return (f'<article class="slide-card {kind}" data-row="video-{esc(j["job"])}">{thumb}<div class="slide-body">'
            f'<h3>{title}</h3><div class="meta">{"<span class=dot>·</span>".join(f"<span class=mi>{x}</span>" for x in meta)}</div>{doc}'
            f'<div class="slide-links">{open_link}</div></div></article>')


def panel(jobs, now, multi, url, period_label):
    if jobs is None:
        return '<div class="card"><h2>作った動画</h2><p class="note inline">動画の記録を取得できませんでした。次の更新で取り直します。</p></div>'
    states = [state(j, now) for j in jobs]
    total = sum(usd(j["log"]) or 0 for j in jobs)
    sub = (f'{len(jobs)}本（できた {states.count("done")}・作成中 {states.count("working")}・失敗 {states.count("error")}）'
           f'・作成費 ${total:.2f}（約{total * 150:,.0f}円）')
    for j in jobs:
        j["day"] = datetime.datetime.fromtimestamp(j["at"], JST).strftime("%Y-%m-%d")
    body = "".join(card(j, now, multi, url) for j in jobs) or f'<p class="note inline">{period_label}に作った動画はありません。</p>'
    return (f'<div class="card"><h2>作った動画<small>{sub}</small></h2>'
            f'<p class="legend">新しい順。ポスターを押すと動画のページが開きます。作成費は動画作成機能の Opus のトークンだけの概算です（読み上げと動画にまとめる処理の分は含みません）。</p>'
            f'<div class="slide-grid">{body}</div></div>')
