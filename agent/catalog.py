"""セッションカタログ：S3 の控えで先に答え、裏で公式カタログ API と照合する。

控え（sessions/latest.json）は Lambda が11月は1時間、会期中は15分ごとに取り直す。
Runtime は会話の開始時に読み込み、検索は手元で行う（2ミリ秒前後）。
照合で見つけた変更は sessions/overrides/<code>.json に書き、次の人から控えに重ねて使う。
"""

import concurrent.futures
import datetime
import json
import re
import threading
import time
import urllib.parse
import urllib.request

import boto3

from settings import JST, KNOWLEDGE_BUCKET, LAS_VEGAS, REGION

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/130 Safari/537.36"}
REG = "https://registration.awsevents.com"
PAGE = REG + "/flow/awsevents/reinvent2026/eventcatalog/page/eventcatalog"
API = "https://catalog.awsevents.com/api/sessions"
SNAPSHOT_TTL = 300
OFFICIAL_TIMEOUT = 4.0

_s3 = boto3.client("s3", region_name=REGION)
_lock = threading.Lock()
_snapshot = {"loaded": 0.0, "retrieved": "", "sessions": {}}
_headers = {"at": 0.0, "value": None}
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="catalog")


# ---- 控え ----

def snapshot():
    with _lock:
        if time.time() - _snapshot["loaded"] < SNAPSHOT_TTL and _snapshot["sessions"]:
            return _snapshot
        body = json.loads(_s3.get_object(Bucket=KNOWLEDGE_BUCKET, Key="sessions/latest.json")["Body"].read())
        sessions = {s["code"]: s for s in body["sessions"]}
        retrieved = body["retrieved"]
        for code, override in _overrides(retrieved).items():
            sessions[code] = override
        _snapshot.update(loaded=time.time(), retrieved=retrieved, sessions=sessions)
        return _snapshot


def _overrides(retrieved):
    """控えより新しい照合結果だけを重ねる（控えが取り直されたら古い分は使わない）。"""
    result = {}
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=KNOWLEDGE_BUCKET, Prefix="sessions/overrides/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"].astimezone(JST).isoformat() <= retrieved:
                continue
            data = json.loads(_s3.get_object(Bucket=KNOWLEDGE_BUCKET, Key=obj["Key"])["Body"].read())
            if data.get("session"):
                result[data["session"]["code"]] = data["session"]
    return result


def _jst(utc_start):
    try:
        t = datetime.datetime.strptime(utc_start, "%Y/%m/%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None
    return t.astimezone(JST).strftime("%m/%d %H:%M")


def view(s, full=False):
    """モデルへ渡す形。時刻はラスベガス時間（カタログの値）と、計算済みの日本時間。"""
    times = [{"date": t["date"], "start": t["start"], "end": t["end"], "jst_start": _jst(t.get("utc_start")),
              "room": t["room"]} for t in s.get("times") or []]
    abstract = s.get("abstract") or ""
    return {
        "code": s["code"], "title": s["title"], "type": s.get("type"),
        "level": (s.get("attributes", {}).get("Level") or [None])[0],
        "times": times,
        "speakers": [f"{p['name']}（{p.get('company') or ''}）" for p in (s.get("speakers") or [])[:4]],
        "abstract": abstract if full else abstract[:240],
        "url": f"{PAGE}?search={urllib.parse.quote(s['code'])}",
    }


WORD = re.compile(r"[a-z0-9][a-z0-9:+.#-]*")


def _hhmm(value):
    """「13:00」「9:30」「13」を HH:MM にそろえる。読めなければ空。"""
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*", value or "")
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m and int(m.group(1)) < 24 else ""


def search(query="", day="", venue="", session_type="", limit=8, start_after="", start_before=""):
    snap = snapshot()
    words = [w for w in WORD.findall(query.lower()) if len(w) > 1]
    codes = {w.upper() for w in words}
    after, before = _hhmm(start_after), _hhmm(start_before)
    hits = []
    for s in snap["sessions"].values():
        # 日付・会場・時刻は同じ回（times の1件）でそろって満たす必要がある
        slots = [t for t in s.get("times") or []
                 if (not day or t.get("date") == day)
                 and (not venue or venue.lower() in (t.get("room") or "").lower())
                 and (not after or (t.get("start") or "") >= after)
                 and (not before or (t.get("start") or "99") < before)]
        if (day or venue or after or before) and not slots:
            continue
        if session_type and session_type.lower() not in (s.get("type") or "").lower():
            continue
        if s["code"] in codes:
            score = 100
        elif words:
            title = (s.get("title") or "").lower()
            abstract = (s.get("abstract") or "").lower()
            people = " ".join(f"{p.get('name')} {p.get('company')}" for p in s.get("speakers") or []).lower()
            score = sum(3 * (w in title) + (w in abstract) + 2 * (w in people) for w in words)
            if score == 0:
                continue
        else:
            score = 1
        first = min((t.get("utc_start") or "9" for t in slots or s.get("times") or []), default="9")
        hits.append((-score, first, s))
    hits.sort(key=lambda h: (h[0], h[1]))
    return snap["retrieved"], len(hits), [h[2] for h in hits[:limit]]


# ---- 公式カタログ API ----

def _get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=OFFICIAL_TIMEOUT).read().decode(
        "utf-8", "ignore")


def _api_headers():
    if _headers["value"] and time.time() - _headers["at"] < 3600:
        return _headers["value"]
    token = re.search(r"workflowApiToken = '([^']+)'", _get(PAGE)).group(1)
    conf = _get(f"{REG}/flow/loadPage?pageUri=eventcatalog&overrideShowDate=false&workflowApiToken={token}")
    profile = re.search(r'"apiProfileToken":"([^"]+)"', conf).group(1)
    widget = re.search(r'data-widgetid=\\"([^"\\]+)', conf).group(1)
    _headers.update(at=time.time(), value={**UA, "rfApiProfileId": profile, "rfWidgetId": widget,
                                           "Content-Type": "application/x-www-form-urlencoded"})
    return _headers["value"]


def _tidy(it):
    attrs = {}
    for a in it.get("attributevalues") or []:
        attrs.setdefault(a.get("attribute"), []).append(a.get("value"))
    return {
        "code": it.get("code"), "id": it.get("sessionID"), "title": it.get("title"), "type": it.get("type"),
        "length_min": it.get("length"), "abstract": it.get("abstract"),
        "times": [{"date": t.get("date"), "start": t.get("startTime"), "end": t.get("endTime"),
                   "utc_start": t.get("utcStartTime"), "room": t.get("room"), "capacity": t.get("capacity")}
                  for t in it.get("times") or []],
        "speakers": [{"name": p.get("fullName"), "company": p.get("companyName"), "title": p.get("jobTitle"),
                      "role": p.get("roles")} for p in it.get("participants") or []],
        "attributes": attrs, "modified": it.get("modified"),
    }


def official_search(text, size=20):
    body = urllib.parse.urlencode({"type": "session", "size": size, "from": 0, "search": text}).encode()
    req = urllib.request.Request(API, data=body, headers=_api_headers())
    d = json.loads(urllib.request.urlopen(req, timeout=OFFICIAL_TIMEOUT).read())
    items = d["items"] if isinstance(d.get("items"), list) else (d.get("sectionList") or [{}])[0].get("items", [])
    return [_tidy(i) for i in items]


def _slot(t):
    return (t.get("date"), t.get("start"), t.get("end"), t.get("room"))


def verify(shown_codes, query):
    """控えで出したセッションを公式 API と突き合わせる。戻り値は追記に使う変更の一覧。"""
    snap = snapshot()
    jobs = {code: _pool.submit(official_search, code, 5) for code in shown_codes[:8]}
    keyword = _pool.submit(official_search, query, 30) if query.strip() else None
    changes, new = [], []
    for code, job in jobs.items():
        try:
            found = next((s for s in job.result() if s["code"] == code), None)
        except Exception as e:  # 公式 API が遅い・落ちているときは、その件は照合しない
            print(json.dumps({"catalog_verify_error": type(e).__name__}))
            return None
        before = snap["sessions"].get(code)
        if not before:
            continue
        if found is None:
            changes.append({"code": code, "title": before["title"], "kind": "removed"})
        elif [_slot(t) for t in found["times"]] != [_slot(t) for t in before["times"]]:
            changes.append({"code": code, "title": found["title"], "kind": "moved",
                            "before": [_slot(t) for t in before["times"]], "after": [_slot(t) for t in found["times"]]})
            _remember(found)
    try:
        extra = keyword.result() if keyword else []
    except Exception:
        extra = []
    if extra:
        for s in extra:
            if s["code"] not in snap["sessions"]:
                new.append(view(s))
                _remember(s)
    return {"checked_at": datetime.datetime.now(JST).strftime("%H:%M"), "changes": changes, "new": new}


def _remember(session):
    _s3.put_object(Bucket=KNOWLEDGE_BUCKET, Key=f"sessions/overrides/{session['code']}.json",
                   Body=json.dumps({"session": session}, ensure_ascii=False).encode(), ContentType="application/json")
    with _lock:
        _snapshot["sessions"][session["code"]] = session


def start_verify(shown_codes, query):
    return _pool.submit(verify, list(shown_codes), query)


def describe(result):
    """照合の結果を、画面へ出すひな形の文にする（モデルに書かせない）。"""
    lines = []
    for c in result["changes"]:
        if c["kind"] == "removed":
            lines.append(f"{c['code']}「{c['title']}」は公式カタログから見つからなくなっています")
        else:
            after = "、".join(f"{d} {s}〜{e} {r}" for d, s, e, r in c["after"])
            lines.append(f"{c['code']} の日時・部屋が変わっています → {after}")
    if result["new"]:
        lines.append(f"ほかに、控えに無い新しいセッションが {len(result['new'])} 件あります（"
                     + "、".join(f"{n['code']}「{n['title']}」" for n in result["new"][:3]) + "）")
    return lines


def las_vegas_now():
    return datetime.datetime.now(LAS_VEGAS)
