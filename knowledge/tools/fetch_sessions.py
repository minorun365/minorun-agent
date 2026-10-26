"""re:Invent 2026 のセッションカタログ（公式・ログイン不要）を丸ごと取り、スナップショットと差分を残す。

カタログ画面（registration.awsevents.com の eventcatalog）は RainFocus の公開API
catalog.awsevents.com/api/sessions を呼んでいる。呼び出しに要る2つのヘッダー
（rfApiProfileId・rfWidgetId）は画面の設定（loadPage）に書かれているので、毎回そこから引き直す。

出力:
  sources/sessions/sessions-<日付>.json   1セッション1レコードに整えた全件（取得時刻つき）
  sources/sessions/latest.json            直近のスナップショット（同じ中身）
  sources/sessions/changes-<日付>.json    前回からの追加・削除・日時や部屋の変更
使い方: python3 tools/fetch_sessions.py
"""
import datetime, json, re, sys, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sources" / "sessions"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/130 Safari/537.36"}
REG = "https://registration.awsevents.com"
PAGE = REG + "/flow/awsevents/reinvent2026/eventcatalog/page/eventcatalog"
API = "https://catalog.awsevents.com/api/sessions"
SIZE = 50


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "ignore")


def headers():
    token = re.search(r"workflowApiToken = '([^']+)'", get(PAGE)).group(1)
    conf = get(f"{REG}/flow/loadPage?pageUri=eventcatalog&overrideShowDate=false&workflowApiToken={token}")
    profile = re.search(r'"apiProfileToken":"([^"]+)"', conf).group(1)
    widget = re.search(r'data-widgetid=\\"([^"\\]+)', conf).group(1)
    return {**UA, "rfApiProfileId": profile, "rfWidgetId": widget,
            "Content-Type": "application/x-www-form-urlencoded"}


def page(h, start):
    body = urllib.parse.urlencode({"type": "session", "size": SIZE, "from": start}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(API, data=body, headers=h), timeout=60).read())
    # 1ページ目は sectionList の中に、2ページ目以降は最上位に items が入る
    if isinstance(d.get("items"), list):
        return d.get("total", 0), d["items"]
    sec = (d.get("sectionList") or [{}])[0]
    return d.get("totalSearchItems", 0), sec.get("items", [])


def tidy(it):
    attrs = {}
    for a in it.get("attributevalues") or []:
        attrs.setdefault(a.get("attribute"), []).append(a.get("value"))
    return {
        "code": it.get("code"), "id": it.get("sessionID"), "title": it.get("title"),
        "type": it.get("type"), "length_min": it.get("length"), "abstract": it.get("abstract"),
        "times": [{"date": t.get("date"), "start": t.get("startTime"), "end": t.get("endTime"),
                   "utc_start": t.get("utcStartTime"), "room": t.get("room"), "capacity": t.get("capacity")}
                  for t in it.get("times") or []],
        "speakers": [{"name": p.get("fullName"), "company": p.get("companyName"),
                      "title": p.get("jobTitle"), "role": p.get("roles")}
                     for p in it.get("participants") or []],
        "attributes": attrs,
        "modified": it.get("modified"),
    }


def diff(old, new):
    o, n = {s["code"]: s for s in old}, {s["code"]: s for s in new}
    moved = [{"code": c, "title": n[c]["title"], "before": o[c]["times"], "after": n[c]["times"]}
             for c in n.keys() & o.keys() if o[c]["times"] != n[c]["times"]]
    return {"added": sorted(n.keys() - o.keys()), "removed": sorted(o.keys() - n.keys()), "time_or_room_changed": moved}


def main():
    h = headers()
    total, items = page(h, 0)
    start = len(items)
    while start < total:
        _, more = page(h, start)
        if not more:
            break
        items += more
        start += len(more)
    sessions = sorted((tidy(i) for i in items), key=lambda s: s["code"] or "")
    if len(sessions) < total:
        sys.exit(f"取得が途中で止まりました：{len(sessions)} / {total} 件")
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    snap = {"retrieved": now.isoformat(timespec="seconds"), "source": PAGE, "total": total, "sessions": sessions}
    OUT.mkdir(parents=True, exist_ok=True)
    latest = OUT / "latest.json"
    if latest.exists():
        ch = diff(json.loads(latest.read_text())["sessions"], sessions)
        (OUT / f"changes-{now.date()}.json").write_text(json.dumps(ch, ensure_ascii=False, indent=1))
        print(f"追加{len(ch['added'])}・削除{len(ch['removed'])}・日時/部屋の変更{len(ch['time_or_room_changed'])}")
    text = json.dumps(snap, ensure_ascii=False, indent=1)
    (OUT / f"sessions-{now.date()}.json").write_text(text)
    latest.write_text(text)
    print(f"{len(sessions)} / {total} 件を取得 → {latest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
