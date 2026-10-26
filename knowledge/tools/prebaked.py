"""用意した質問の事前回答（knowledge/prebaked/<topic>.json）の検査と、エージェントへの写し。

  python3 knowledge/tools/prebaked.py check [topic ...] [--stamp]   形式・長さ・出典を検査（--stamp で事実の指紋を記録）
  python3 knowledge/tools/prebaked.py stale                          知識が変わって作り直しが要る話題を出す
  python3 knowledge/tools/prebaked.py build                          指紋が今の知識と一致する話題だけを agent/prebaked.json へ写す

1ファイルの形：
{"topic": "...", "facts_hash": "...", "items": [
  {"q": "用意した質問", "heading": "話題の見出し（15字以内）", "tools": ["search_2026"], "map": null,
   "answer": "回答（Markdown・出典は [ラベル](URL)）",
   "next": [{"q": "深掘りの質問？", "tools": [...], "map": null, "answer": "..."}, ...4つ]}],
 "skipped": [{"q": "...", "reason": "..."}]}
"""

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOW = ROOT / "knowledge"
DIR = KNOW / "prebaked"
OUT = ROOT / "agent" / "prebaked.json"
TOOLS = {"search_2026", "search_past"}
MAPS = {m["id"] for m in json.loads((ROOT / "agent" / "maps.json").read_text())["images"]}
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
# 利用者に見せない内部の言葉。アプリの作りを答える話題だけは仕組みの言葉を使ってよい
BANNED = ("筆者", "著者", "search_", "詳しくは次", "確認役", "照合済み", "調べましょうか")
INTERNAL = ("ナレッジベース", "ツール")
NEXT_COUNT = 4


def facts_hash(topic):
    """話題のまとめのうち、事実の行だけの指紋（作成日の行が変わっても変えない）。"""
    path = KNOW / "entries" / f"{topic}.md"
    lines = [l for l in path.read_text().splitlines() if l.startswith("- ")]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def known_urls():
    urls = set()
    for p in (KNOW / "entries").glob("*.md"):
        urls.update(re.findall(r"\((https?://[^)\s]+)\)", p.read_text()))
    return urls


def starters():
    gen = json.loads((KNOW / "starters.json").read_text())
    cur = json.loads((KNOW / "starters-curated.json").read_text())
    return {t: list(dict.fromkeys([*gen.get(t, {}).get("questions", []), *cur.get(t, [])]))
            for t in sorted(set(gen) | set(cur))}


def plain(answer):
    """画面に出る文字数（出典はラベルだけ・太字の記号と空白を除く）。"""
    return re.sub(r"\s|\*\*", "", LINK.sub(lambda m: "", answer))


def check_answer(where, a, topic, urls):
    errs = []
    text = a.get("answer", "")
    if not text.strip():
        return [f"{where}: 回答が空"]
    n = len(plain(text))
    if n > 150 or n < 30:
        errs.append(f"{where}: 本文が{n}字（30〜150字）")
    links = LINK.findall(text)
    if len(links) > 2:
        errs.append(f"{where}: 出典が{len(links)}個（2つまで）")
    for _, url in links:
        if url not in urls:
            errs.append(f"{where}: 知識に無いURL {url}")
    if re.search(r"(?<!\])\(?https?://", LINK.sub("", text)):
        errs.append(f"{where}: URLを本文にそのまま書いている")
    # アプリの作りと技術ナレッジの話題では「ツール」「ナレッジベース」が本来の技術用語なので禁じない
    words = BANNED + (() if topic == "about-this-app" or topic.startswith("tech-") or topic in ("kag-tech", "recent-updates") else INTERNAL)
    errs += [f"{where}: 使わない言葉「{w}」" for w in words if w in text]
    if re.search(r"^#|^\|", text, re.M):
        errs.append(f"{where}: 見出しや表がある")
    tools = a.get("tools")
    if not tools or not set(tools) <= TOOLS:
        errs.append(f"{where}: tools は search_2026・search_past から1つ以上")
    if a.get("until") is not None and not re.fullmatch(r"2\d{3}-\d{2}-\d{2}", str(a["until"])):
        errs.append(f"{where}: until は YYYY-MM-DD")
    if a.get("map") not in (None, *MAPS):
        errs.append(f"{where}: 未登録の画像 {a.get('map')}")
    return errs


def check(topic, stamp=False, urls=None):
    urls = urls or known_urls()
    path = DIR / f"{topic}.json"
    if not path.exists():
        return [f"{topic}: ファイルが無い"]
    data = json.loads(path.read_text())
    errs = []
    expected = set(starters().get(topic, []))
    done = {i["q"] for i in data.get("items", [])} | {s["q"] for s in data.get("skipped", [])}
    errs += [f"{topic}: 未処理の質問「{q}」" for q in sorted(expected - done)]
    errs += [f"{topic}: 用意した質問に無い「{q}」" for q in sorted(done - expected)]
    for s in data.get("skipped", []):
        if not s.get("reason"):
            errs.append(f"{topic}: 外した理由が無い「{s['q']}」")
    for item in data.get("items", []):
        q = item["q"]
        # 画面の候補と同じ上限（tests/web/starters.test.ts）
        if len(q) > 22:
            errs.append(f"{q}: 用意した質問は22字以内")
        if re.search(r"教えて|ですか|ますか|りる？|る？る？", q):
            errs.append(f"{q}: 依頼形・崩れた語尾に見える")
        if not 1 <= len(item.get("heading", "")) <= 15:
            errs.append(f"{q}: heading は1〜15字")
        errs += check_answer(q, item, topic, urls)
        nexts = item.get("next", [])
        if len(nexts) != NEXT_COUNT:
            errs.append(f"{q}: 深掘りが{len(nexts)}個（{NEXT_COUNT}つ）")
        seen = set()
        for nx in nexts:
            nq = nx.get("q", "")
            if not nq.endswith("？") or not 6 <= len(nq) <= 28:
                errs.append(f"{q} → {nq}: 深掘りの質問は28字以内で「？」で終える")
            if nq in seen or nq == q:
                errs.append(f"{q} → {nq}: 質問が重複")
            seen.add(nq)
            errs += check_answer(f"{q} → {nq}", nx, topic, urls)
    if not errs and stamp:
        data["facts_hash"] = facts_hash(topic)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return errs


def stale():
    out = []
    for path in sorted(DIR.glob("*.json")):
        data = json.loads(path.read_text())
        if data.get("facts_hash") != facts_hash(data["topic"]):
            out.append(data["topic"])
    return out


def build():
    """指紋が一致する話題だけを写す。深掘りは親の質問に結び付け、親を答えた直後だけ使う。"""
    bad = set(stale())
    first, follow = {}, {}
    for path in sorted(DIR.glob("*.json")):
        data = json.loads(path.read_text())
        if data["topic"] in bad:
            continue
        for item in data["items"]:
            first[item["q"]] = {"topic": data["topic"], "heading": item["heading"], "tools": item["tools"],
                                "map": item.get("map"), "answer": item["answer"], "until": item.get("until"),
                                "next": [n["q"] for n in item["next"]]}
            for n in item["next"]:
                follow[f"{item['q']}\n{n['q']}"] = {"tools": n["tools"], "map": n.get("map"), "answer": n["answer"], "until": n.get("until")}
    OUT.write_text(json.dumps({"first": first, "follow": follow}, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(first), len(follow), sorted(bad)


if __name__ == "__main__":
    cmd, args = (sys.argv[1:] or ["check"])[0], [a for a in sys.argv[2:] if not a.startswith("--")]
    if cmd == "check":
        urls = known_urls()
        topics = args or [p.stem for p in sorted(DIR.glob("*.json"))]
        failed = False
        for t in topics:
            errs = check(t, "--stamp" in sys.argv, urls)
            failed |= bool(errs)
            print(f"{t}: {'OK' if not errs else f'{len(errs)}件'}")
            for e in errs[:60]:
                print("  - " + e)
        sys.exit(1 if failed else 0)
    if cmd == "stale":
        print("\n".join(stale()) or "（作り直しの要る話題なし）")
    elif cmd == "build":
        n1, n2, bad = build()
        print(f"1問目 {n1}問・2問目 {n2}問を {OUT.relative_to(ROOT)} へ写しました" + (f"（古いので外した話題: {', '.join(bad)}）" if bad else ""))
