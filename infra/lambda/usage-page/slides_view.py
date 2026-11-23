"""利用者が作ったスライドを、監視ダッシュボードの「スライド」タブにサムネイルつきで並べる。

正本は3か所に分かれている。
  - 表 minorun-agent-slides の list#<利用者> … 番号と受け付けた時刻（記事のURLは残さない作り）
  - 公開用 S3 の s/<番号>/status.json … 進み具合・題・枚数（画面のサムネイルと同じもの）
  - スライド作成機能のログ … 1本ごとの費用（Opus のトークン）・かかった秒数・直した回数・失敗の理由
頼んだ質問の文（記事のURLを含む）は1問1行の表から、同じ利用者で make_slides を使った一番近い質問を探して添える。
"""
import concurrent.futures
import datetime
import html
import json
import os
import re
import urllib.parse

import boto3

TABLE = os.environ.get("SLIDES_TABLE", "")
BUCKET = os.environ.get("SLIDES_BUCKET", "")
# スライド作成機能の最後の1行を読むロググループ。いまは Runtime のもの、2026-11-15 分は Lambda のころのもの（カンマ区切り）
LOG_GROUPS = [g.strip() for g in os.environ.get("SLIDES_LOG_GROUP", "").split(",") if g.strip()]
JST = datetime.timezone(datetime.timedelta(hours=9))
STAGES = {"queued": "順番待ち", "reading": "記事を読んでいる", "writing": "書いている", "rendering": "焼いている",
          "checking": "余白を測っている", "fixing": "崩れを直している"}
# スライド作成の Runtime は1本20分で打ち切る。それより長く途中のままなのは、落ちて status を書けなかったもの
STALE = 20 * 60
MATCH = datetime.timedelta(minutes=15)
URL = re.compile(r"https?://[^\s<>\"'）」]+")


def _jobs(start, end):
    ddb = boto3.client("dynamodb")
    args = {"TableName": TABLE, "FilterExpression": "begins_with(pk, :p)", "ExpressionAttributeValues": {":p": {"S": "list#"}}}
    out = []
    while True:
        page = ddb.scan(**args)
        for item in page["Items"]:
            user = item["pk"]["S"][5:]
            for j in item.get("jobs", {}).get("L", []):
                at = int(j["M"]["at"]["N"])
                if start.timestamp() <= at < end.timestamp():
                    out.append({"job": j["M"]["job"]["S"], "at": at, "user": user})
        if not page.get("LastEvaluatedKey"):
            return sorted(out, key=lambda j: -j["at"])
        args["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _status(job):
    try:
        body = boto3.client("s3").get_object(Bucket=BUCKET, Key=f"s/{job}/status.json")["Body"].read()
        return json.loads(body)
    except Exception:
        return {"stage": "unknown"}


# ロググループごとに、読み終えた範囲 [始め, 終わり) とその中の行を覚えておく（温まった Lambda の中だけ）。
# 15秒ごとの部分更新のたびに公開日から全部を探し直すと、空のページを何十回もめくって数秒かかるため、
# 2回目からは覚えた範囲の後ろだけを読む。ログは数分遅れて届くことがあるので、直近の分は覚えない。
_SEEN = {}
SETTLE_MS = 10 * 60 * 1000


def _filter(logs, group, start_ms, end_ms):
    args = {"logGroupName": group, "filterPattern": "{ $.job = * && ($.ok IS TRUE || $.ok IS FALSE) }",
            "startTime": start_ms, "endTime": end_ms}
    out = []
    while True:
        page = logs.filter_log_events(**args)
        for e in page.get("events", []):
            try:
                out.append((e["timestamp"], json.loads(e["message"][e["message"].index("{"):])))
            except ValueError:
                continue
        token = page.get("nextToken")
        if not isinstance(token, str) or not token or token == args.get("nextToken"):
            return out
        args["nextToken"] = token


def _group(group, start_ms, end_ms, now_ms):
    logs = boto3.client("logs")
    try:
        seen = _SEEN.get(group)
        if seen and seen[0] <= start_ms:
            begin, events = seen[1], list(seen[2])
        else:
            begin, events = start_ms, []
        settled = max(begin, min(end_ms, now_ms - SETTLE_MS))
        if settled > begin:
            events += _filter(logs, group, begin, settled)
            _SEEN[group] = (seen[0] if seen and seen[0] <= start_ms else start_ms, settled, events)
            events = list(events)
        if end_ms > settled:
            events += _filter(logs, group, settled, end_ms)
        return [(ts, rec) for ts, rec in events if start_ms <= ts < end_ms]
    except Exception as e:
        print(json.dumps({"slides_logs": "unavailable", "group": group, "error": type(e).__name__}))
        return []


def _finals(start, end):
    """スライド作成機能が1本ごとに最後に出す1行（job と ok を持つ JSON）を、(時刻ミリ秒, 中身) で順に返す。"""
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    now_ms = int(datetime.datetime.now(JST).timestamp() * 1000)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(LOG_GROUPS))) as pool:
        for events in pool.map(lambda g: _group(g, start_ms, end_ms, now_ms), LOG_GROUPS):
            yield from events


def _logs(start, end):
    """最後の1行を番号で引けるようにする。"""
    return {rec.get("job"): rec for _, rec in _finals(start, end + datetime.timedelta(minutes=20))}


# スライド作成機能が Claude API で書いた分の単価（100万トークンあたりのドル。Bedrock の Opus 5.5 と同じ）
API_MODEL = "claude-opus-5-5"
API_PRICE = {"input": 4.00, "output": 20.00}


def api_usd_by_hour(start, end):
    """スライド作成機能が Claude API（Anthropic のクレジット）で払った Opus の費用を、日本時間の時台ごとに返す。
    AWS/Bedrock のメトリクスには載らないので、1本ごとの最後のログ行の usage（呼び出しごとのモデル）から足す。"""
    out = {}
    for ts, rec in _finals(start, end):
        usd = sum((u.get("input", 0) * API_PRICE["input"] + u.get("output", 0) * API_PRICE["output"]) / 1e6
                  for u in rec.get("usage") or [] if isinstance(u, dict) and u.get("model") == API_MODEL)
        h = datetime.datetime.fromtimestamp(ts / 1000, JST).replace(minute=0, second=0, microsecond=0)
        out[h] = out.get(h, 0.0) + usd
    return out


def load(start, end):
    """選んだ期間に受け付けたスライド。取れなければ None（0本と表示しない）。"""
    if not (TABLE and BUCKET):
        return None
    try:
        jobs = _jobs(start, end)
    except Exception:
        print(json.dumps({"slides_table": "unavailable"}))
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


def attach(jobs, rows):
    """同じ利用者で make_slides を使った質問のうち、受け付けた時刻に一番近いものを添える。
    スライドの持ち主はログインした人なら user、していない人なら「a:匿名ID」（agent/slides.py の owner）なので、質問の側も同じ形にそろえて比べる。"""
    def owner(r):
        return r.get("user") or (f"a:{r['anon']}" if r.get("anon") else "")
    asked = [(owner(r), r) for r in rows if owner(r) and "make_slides" in (r.get("tools") or [])]
    used = set()
    for j in sorted(jobs, key=lambda x: x["at"]):
        at = datetime.datetime.fromtimestamp(j["at"], JST)
        best = None
        for who, r in asked:
            if who != j["user"] or id(r) in used:
                continue
            gap = abs(datetime.datetime.fromisoformat(r["created_at"]) - at)
            if gap <= MATCH and (best is None or gap < best[0]):
                best = (gap, r)
        if best:
            used.add(id(best[1]))
            j["row"] = best[1]
    return jobs


def state(j, now):
    st = j["status"]
    stage = st.get("stage", "unknown")
    if stage == "done":
        return "done"
    if stage == "error":
        return "error"
    if stage in STAGES and now.timestamp() - st.get("at", j["at"]) > STALE:
        return "error"
    return "working" if stage in STAGES else "unknown"


def totals(jobs, now):
    states = [state(j, now) for j in jobs]
    usd = sum(j["log"].get("usd") or 0 for j in jobs)
    return {"count": len(jobs), "done": states.count("done"), "error": states.count("error"),
            "working": states.count("working"), "usd": usd}


def _when(ts, multi):
    t = datetime.datetime.fromtimestamp(ts, JST)
    head = f"{t.month}/{t.day}（{'月火水木金土日'[t.weekday()]}）" if multi else ""
    return head + t.strftime("%H:%M")


def _ask(question, mask):
    """頼んだ質問の文。記事のURLは元の記事へのリンクにし、伏せ字はURL以外の部分にだけかける（URLの数字の並びを電話番号と見なさない）。"""
    out, last = [], 0
    for found in URL.finditer(question):
        out.append(html.escape(mask(question[last:found.start()]) if question[last:found.start()].strip() else question[last:found.start()]))
        link = found.group(0)
        u = urllib.parse.urlparse(link)
        label = u.netloc + (u.path if len(u.path) <= 24 else u.path[:23] + "…")
        out.append(f'<a href="{html.escape(link)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>')
        last = found.end()
    rest = question[last:]
    out.append(html.escape(mask(rest) if rest.strip() else rest))
    return "".join(out)


def card(j, now, multi, mask, url):
    esc = html.escape
    st, log = j["status"], j["log"]
    kind = state(j, now)
    base = f"/s/{j['job']}/"
    row = j.get("row") or {}
    identifier = "G-" + j["user"]
    if kind == "done":
        thumb = (f'<a class="slide-thumb" href="{esc(base)}" target="_blank" rel="noopener">'
                 f'<img src="{esc(base)}og.jpg" alt="" loading="lazy" width="1200" height="675"></a>')
        title = esc(st.get("title") or "（題なし）")
    elif kind == "error":
        reason = st.get("message") or "途中で止まり、結果を書けませんでした。"
        thumb = f'<div class="slide-thumb empty bad"><span>作れなかった</span></div>'
        title = f'<span class="warn">{esc(reason)}</span>'
    else:
        thumb = f'<div class="slide-thumb empty"><span>{esc(STAGES.get(st.get("stage"), "状態不明"))}…</span></div>'
        title = "作成中"
    meta = [_when(j["at"], multi), f'<a href="{esc(url(v="list", d=j["day"], u=identifier))}">{esc(identifier)}</a>']
    if st.get("pages"):
        meta.append(f'{int(st["pages"])}枚')
    if log.get("seconds"):
        meta.append(f'{round(log["seconds"])}秒')
    if log.get("rounds", 1) > 1:
        meta.append("1回直した")
    if log.get("usd") is not None:
        meta.append(f'${log["usd"]:.2f}')
    if kind == "error" and log.get("error"):
        meta.append(esc(log["error"]))
    ask = (f'<p class="slide-ask">{_ask(row.get("question") or "", mask)}</p>' if row.get("question") else
           '<p class="slide-ask none">頼んだ質問を見つけられませんでした</p>')
    open_link = f'<a class="slide-open" href="{esc(base)}" target="_blank" rel="noopener">ビューアで開く ›</a>' if kind == "done" else ""
    return (f'<article class="slide-card {kind}" data-row="slide-{esc(j["job"])}">{thumb}<div class="slide-body">'
            f'<h3>{title}</h3><div class="meta">{"<span class=dot>·</span>".join(f"<span class=mi>{x}</span>" for x in meta)}</div>{ask}'
            f'<div class="slide-links">{open_link}</div></div></article>')


def panel(jobs, now, multi, mask, url, period_label):
    if jobs is None:
        return '<div class="card"><h2>作ったスライド</h2><p class="note inline">スライドの記録を取得できませんでした。次の更新で取り直します。</p></div>'
    t = totals(jobs, now)
    sub = f'{t["count"]}本（できた {t["done"]}・作成中 {t["working"]}・失敗 {t["error"]}）・作成費 ${t["usd"]:.2f}（約{t["usd"] * 150:,.0f}円）'
    for j in jobs:
        j["day"] = datetime.datetime.fromtimestamp(j["at"], JST).strftime("%Y-%m-%d")
    body = "".join(card(j, now, multi, mask, url) for j in jobs) or f'<p class="note inline">{period_label}に作ったスライドはありません。</p>'
    return (f'<div class="card"><h2>作ったスライド<small>{sub}</small></h2>'
            f'<p class="legend">新しい順。サムネイルを押すとビューアが開きます。作成費はスライド作成機能の Opus のトークンだけの概算です。</p>'
            f'<div class="slide-grid">{body}</div></div>')


CSS = """
/* タブ名のあとの本数は、名前と区別できる小さな丸い札にする */
nav.tabs a .tab-count{display:inline-block;min-width:18px;margin-left:6px;padding:0 6px;border-radius:9px;background:#eef1f5;color:var(--sub);font-size:11px;font-weight:600;line-height:18px;text-align:center;vertical-align:1px}
nav.tabs a.on .tab-count{background:var(--accent);color:#fff}
.slide-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;padding:0 20px 20px}
.slide-card{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff;display:flex;flex-direction:column;min-width:0}
.slide-card.error{border-color:#f3c4c0}
.slide-thumb{display:block;aspect-ratio:16/9;background:#eef4fd}
.slide-thumb img{display:block;width:100%;height:100%;object-fit:cover}
.slide-thumb.empty{display:flex;align-items:center;justify-content:center;color:var(--sub);font-size:13px}
.slide-thumb.bad{background:var(--warn-soft);color:var(--warn);font-weight:600}
.slide-body{padding:14px 16px 16px;display:flex;flex-direction:column;gap:6px;min-width:0;flex:1}
.slide-body h3{font-size:15px;font-weight:600;margin:0;line-height:1.5;overflow-wrap:anywhere}
.slide-body .meta{margin-top:0}.slide-body .meta .mi{white-space:nowrap}
.slide-ask{font-size:13px;line-height:1.7;margin:4px 0 0;color:var(--ink);overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.slide-ask.none{color:var(--sub)}
.slide-links{display:flex;justify-content:flex-end;gap:12px;flex-wrap:wrap;margin-top:auto;padding-top:6px;font-size:12px}
.slide-card .warn{color:var(--warn)}
@media(max-width:599px){.slide-grid{grid-template-columns:1fr;padding:0 12px 12px;gap:14px}}
"""
