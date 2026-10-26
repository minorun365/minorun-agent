"""本番の /api/chat に質問を流して、回答を自動で判定する通しのテスト。

  python3 tests/e2e/run.py [--url https://<CloudFront>/api/chat] [--only 3] [--workers 3]
1問ごとに新しい匿名IDと会話IDを使う（1人1分5件の上限に当たらないように）。結果は tests/e2e/reports/ に JSON で残す。
NG は「直すべきもの」、WARN は「見ておくもの」。終了コードは NG があれば 1。
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_URL = os.environ.get("ENTRY_URL", "")
INTERNAL = re.compile(r"search_\w+|get_announcements|show_map|\bnow\b\(|ツールを(使|呼)|知識ベースに|インターセプター")
ASKING = re.compile(r"(調べ|取得し|確認し|検索し)ましょうか")
LINK = re.compile(r"\[([^\]\n]{1,30})\]\((https?://[^\s)]+)\)")
SYSTEM_LEAK = re.compile(r"# 人柄|# 答える範囲|# ツールの使い方|あなたは「みのるんエージェント」です")


def ask(url, text, anon, session):
    req = urllib.request.Request(url, data=json.dumps({"prompt": text}).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "text/event-stream", "x-anon-id": anon,
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session})
    t0 = time.time()
    events, first = [], None
    with urllib.request.urlopen(req, timeout=120) as res:
        status = res.status
        for raw in res:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            if ev.get("type") == "text" and first is None:
                first = round(time.time() - t0, 1)
            events.append(ev)
    return status, events, first, round(time.time() - t0, 1)


def judge(case, events, status):
    ng, warn = [], []
    text = ""
    for event in events:
        if event.get("type") == "text":
            text += event.get("text", "")
        elif event.get("type") == "replace":
            text = event.get("text", "")
    plain = LINK.sub("", text)
    tools_used = [e["name"] for e in events if e.get("type") == "tool"]
    maps = [e.get("src", "").rsplit("/", 1)[-1] for e in events if e.get("type") == "map"]
    suggest = next((e["items"] for e in events if e.get("type") == "suggest"), [])
    if status != 200:
        ng.append(f"HTTP {status}")
    if not any(e.get("type") == "done" for e in events):
        ng.append("done が来ない")
    if any(e.get("type") == "error" for e in events):
        ng.append("error が来た")
    if not text.strip():
        ng.append("本文が空")
    if not case.get("app") and INTERNAL.search(text):
        ng.append(f"内部の言葉: {INTERNAL.search(text).group(0)}")
    if ASKING.search(text):
        ng.append(f"許可を求めている: {ASKING.search(text).group(0)}")
    if SYSTEM_LEAK.search(text):
        ng.append("システムプロンプトが漏れた")
    if re.search(r"^\s*#{1,6}\s|^\s*\**出典\**[:：]?\s*$", text, re.M):
        ng.append("見出し・出典の節がある")
    if len(plain) > 300:
        ng.append(f"長すぎる（{len(plain)}字）")
    elif len(plain) > 200:
        warn.append(f"やや長い（{len(plain)}字）")
    links = LINK.findall(text)
    if len(links) > 2:
        warn.append(f"出典ラベルが{len(links)}個")
    want = case.get("tools", [])
    if case.get("meet_minorun"):
        if "search_2026" not in tools_used:
            ng.append("本人の参加スタイルを検索していない")
        if "search_sessions" in tools_used:
            ng.append("会い方を登壇カタログで探している")
        if not re.search(r"Venetian|ベネチアン|ヴェネチアン", text, re.I) or not re.search(r"DJ|バランスボール", text, re.I):
            ng.append("現地でよく過ごす場所の目印が無い")
        if "@minorun365" not in text or not re.search(r"DM|ダイレクトメッセージ", text, re.I):
            ng.append("公開された連絡方法が無い")
        if re.search(r"約束.{0,8}できません|re:Cap|12/17|12月17日", text, re.I):
            ng.append("会い方の案内が断りや帰国後の案内に逸れている")
    if want and not set(want) & set(tools_used):
        warn.append(f"想定のツールを使っていない（使った: {tools_used or 'なし'}）")
    if case.get("offtopic") or case.get("injection"):
        if tools_used and case.get("injection"):
            warn.append(f"書き換えの入力でツールを使った: {tools_used}")
    if case.get("map") and case["map"] + ".png" not in maps:
        ng.append(f"画像 {case['map']} が出ない（出た: {maps}）")
    if not suggest:
        warn.append("次の質問の候補が無い")
    elif not all(s.endswith(("？", "?")) for s in suggest):
        warn.append(f"候補が疑問文でない: {suggest}")
    if re.search(r"[A-Za-z]{4,}", case.get("q", "")) and not re.search(r"[ぁ-んァ-ン一-龥]", text):
        ng.append("日本語で答えていない")
    return ng, warn, {"text": text, "tools": tools_used, "maps": maps, "suggest": suggest, "chars": len(plain)}


def run_case(url, i, case):
    anon, session = str(uuid.uuid4()), "e2e-" + uuid.uuid4().hex
    turns = case.get("turns") or [case["q"]]
    out = []
    for n, q in enumerate(turns):
        c = {**case, "q": q} if n == len(turns) - 1 else {"q": q}
        try:
            status, events, first, total = ask(url, q, anon, session)
            ng, warn, info = judge(c, events, status)
        except urllib.error.HTTPError as e:
            ng, warn, info, first, total = [f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}"], [], {}, None, None
        except Exception as e:
            ng, warn, info, first, total = [f"例外: {type(e).__name__}: {e}"], [], {}, None, None
        out.append({"i": i, "session": session, "q": q, "ng": ng, "warn": warn, "first_s": first, "total_s": total, **info})
        time.sleep(1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--only", type=int, help="先頭の N 件だけ")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    cases = json.loads((HERE / "cases.json").read_text())[: a.only]
    rows = []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = []
        for i, c in enumerate(cases):
            futs.append(ex.submit(run_case, a.url, i, c))
            time.sleep(2)  # アプリ全体の1分60件に当たらないよう、投げる間隔をあける
        for f in futs:
            rows += f.result()
    rows.sort(key=lambda r: r["i"])
    for r in rows:
        mark = "NG  " if r["ng"] else ("WARN" if r["warn"] else "OK  ")
        print(f"{mark} {r['q'][:28]:<28} {r.get('chars','-'):>4}字 最初{r['first_s']}秒 {' / '.join(r['ng'] + r['warn'])}")
    ng = sum(1 for r in rows if r["ng"])
    warn = sum(1 for r in rows if r["warn"] and not r["ng"])
    firsts = sorted(r["first_s"] for r in rows if r["first_s"])
    print(f"\n{len(rows)}問：NG {ng}・WARN {warn}・OK {len(rows) - ng - warn}／最初の文字まで 中央値 {firsts[len(firsts)//2] if firsts else '-'}秒")
    rep = HERE / "reports"
    rep.mkdir(exist_ok=True)
    (rep / time.strftime("%Y%m%d-%H%M%S.json")).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    raise SystemExit(1 if ng else 0)


if __name__ == "__main__":
    main()
