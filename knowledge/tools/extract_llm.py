"""本文の控え（cache）から、ナレッジベースへ入れる事実を Sonnet 5.5 で書き起こす。

2つの使い方がある。
- own：みのるん本人の Qiita 記事・登壇資料・GitHub の公開リポジトリ（sources/own/index.jsonl）→ sources/own/<id>.json
  みのるんの技術ナレッジを答えるための素材。事実の年は公開した年（その時点の情報なので）
- decks：参加者の Speaker Deck（candidates.jsonl の speakerdeck）→ sources/extracted/<id>.json
  スライド画像の書き起こし（<id>.ocr.txt）を使う。すでに抽出がある資料は、前の事実も渡して作り直す
- articles <id>...：candidates.jsonl の記事（本文は tools/fetch_bodies.py が取る <id>.txt）→ sources/extracted/<id>.json
- kag：KAG の公開情報（sources/kag/index.jsonl。tools/fetch_kag.py が作る）→ sources/kag/<id>.json
  記事の年と違う年を扱う事実（2026年の準備資料にある「去年はこうだった」など）は year_overrides.json へ足す
本文は <id>.ocr.txt（画像の書き起こし）があればそれを、無ければ <id>.txt を使う。作り直すときは --force。
使い方: AWS_PROFILE=sandbox uv run --with boto3 python tools/extract_llm.py own|decks [id...] [--since 2024-01-01] [--force]
"""
import concurrent.futures as cf, datetime, json, re, sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "sources" / "cache"
TOPICS = json.loads((ROOT / "topics.json").read_text())
OVERRIDES = ROOT / "tools" / "year_overrides.json"
BATCH = ROOT / "sources" / "batches" / "b37-speakerdeck-ocr.txt"
MODEL = "global.anthropic.claude-sonnet-5-5"
MAX_CHARS = 60000
bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")

COMMON = """# 事実の書き方
- 1つの事実は120字以内の1文。資料の文を写さず、自分の言葉で書き直す
- 単独で読んで意味が通るように書く（「この資料では」「上記の」などは使わない。サービス名・年・場所を省かない）
- 推測で補わない。資料に無いことは書かない
- time_sensitive：会場・時刻・価格・ルール・仕様・料金・バージョンなど、時期で変わりうる事実なら true
- year：その事実が扱う年（数値）
- topic は次の一覧のキーから1つ選ぶ
{topics}

出力は次の JSON だけ（コードブロックにしない）：
{{"summary": "資料の要旨を自分の言葉で1〜2文", "usefulness": 0〜3, "event_year": 数値, "facts": [{{"topic": "...", "text": "...", "time_sensitive": true, "year": 2026}}]}}"""

OWN = """あなたは「みのるんエージェント」のナレッジベースの編集係です。以下は、みのるん（御田 稔、KDDIアジャイル開発センターのテックエバンジェリスト、AWS AI Hero）本人が公開した{kind}です。
利用者が「みのるんならどう作る？」「◯◯のやり方は？」と聞いたときに答えられるよう、みのるんの技術ナレッジを事実として抜き出します。

# 抜き出すもの（多くても15個。中身の濃い順）
- 何をどう作るか・どう使うかの要点（手順の勘所、設定、選び方、ハマりどころと対処、比較の結論）
- みのるんの意見・勧め・体験は「みのるんは〜を勧めている」「みのるんは〜した」と書く
- 登壇資料なら、伝えたい主張と根拠。リポジトリなら、何をするものか・構成・使っている技術・使い方
- 宣伝・自己紹介・告知・謝辞・目次だけの内容は入れない
- 公開日は {published}。year は原則この年。re:Invent など特定の年のイベントの発表を扱う事実だけ、その開催年にする
- topic は tech-agents・tech-bedrock・tech-ai-work・tech-output を中心に選ぶ。re:Invent の参加の実務や発表のまとめなら、そちらの話題を選んでよい
- usefulness：技術ナレッジとしての有用度。event_year は公開年
""" + COMMON

DECKS = """あなたは「みのるんエージェント」のナレッジベースの編集係です。以下は、{what}です。
日本から re:Invent に参加する人に役立つ事実を抜き出します。参加者どうしの情報共有（準備・移動・会場・食事・ホテル・お金・通信・英語・コミュニティ・現地の様子・帰国後の活かし方）を特に丁寧に拾います。

# 抜き出すもの（多くても20個）
- 参加の実務・現地の様子・コミュニティ・発表の概要。個別セッションの技術解説の中身は入れない
- 書き手の体験・意見は「筆者は〜した」「〜という体験談がある」と書き、一般的な事実と区別する
- 公開日は {published}。event_year は資料が主に扱う re:Invent の開催年（2026年に向けた準備の資料なら2026）。year は事実ごとに、その事実が扱う開催年（2026年の準備資料が「2025年はこうだった」と語るなら2025）
- usefulness：参加者向け知識としての有用度 0〜3（0 は事実なし）
{previous}""" + COMMON


KAGP = """あなたは「みのるんエージェント」のナレッジベースの編集係です。みのるんの所属する KDDIアジャイル開発センター（KAG）が公開した{kind}を読み、
利用者に KAG のことを聞かれたときに答えられる事実を抜き出します。

# 抜き出すもの（多くても12個）
- KAG の事業・サービス・導入事例（顧客名は公開されているとおり）・ニュース・登壇・受賞・社内文化・働き方・採用・技術の取り組み
- Qiita の技術記事なら、その技術の要点（何をどうすると何がうれしいか・ハマりどころ）。書き手は「KAG のエンジニア」と書く（個人名は書かない）
- 日付のある出来事は日付を書く。宣伝文句・お問い合わせ先・定型の会社概要の繰り返しは入れない
- 公開日は {published}。year は原則この年（公開日が無ければ2026）
- topic は about-minorun-kag・kag-activities・kag-tech から選ぶ
- usefulness：利用者への有用度 0〜3。event_year は公開年
""" + COMMON


def ask(system, text):
    r = bedrock.converse(modelId=MODEL, system=[{"text": system}], messages=[{"role": "user", "content": [{"text": text}]}],
                         inferenceConfig={"maxTokens": 12000},
                         additionalModelRequestFields={"output_config": {"effort": "medium"}})
    out = "".join(c.get("text", "") for c in r["output"]["message"]["content"])
    return json.loads(out[out.find("{"): out.rfind("}") + 1])


def body(i):
    for name in (f"{i}.ocr.txt", f"{i}.txt"):
        if (CACHE / name).exists():
            return (CACHE / name).read_text()[:MAX_CHARS]
    return ""


def topics_list(keys):
    return "\n".join(f"- {k}: {TOPICS[k]}" for k in keys)


def clean(facts, allowed):
    keep = []
    for f in facts:
        t = (f.get("text") or "").strip()
        if f.get("topic") in allowed and t and len(t) <= 120 and isinstance(f.get("year"), int):
            keep.append({"topic": f["topic"], "text": t, "time_sensitive": bool(f.get("time_sensitive")), "year": f["year"]})
    return keep


def do_own(r, force):
    out = ROOT / "sources" / "own" / f"{r['id']}.json"
    if out.exists() and not force:
        return r["id"], "skip"
    text = body(r["id"])
    if not text:
        return r["id"], "本文なし"
    kind = {"article": "Qiita の技術記事", "slides": "登壇資料（スライド画像の書き起こし）", "repo": "GitHub の公開リポジトリの README"}[r["kind"]]
    sysp = OWN.format(kind=kind, published=r["published"], topics=topics_list(TOPICS))
    d = ask(sysp, text)
    py = int(r["published"][:4])
    facts = clean(d.get("facts", []), TOPICS)
    for f in facts:  # 年は公開年が基本。先の年は付けない
        f["year"] = min(f["year"], py) if f["year"] else py
    rec = {"id": r["id"], "url": r["url"], "title": r["title"], "site": r["site"], "author": "みのるん（minorun365）",
           "published": r["published"], "retrieved": datetime.date.today().isoformat(), "source_type": "own",
           "kind": r["kind"], "event_year": py, "usefulness": d.get("usefulness", 0), "summary": d.get("summary", ""),
           "facts": facts}
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    return r["id"], f"{len(facts)}件"


def do_deck(c, force):
    out = ROOT / "sources" / "extracted" / f"{c['id']}.json"
    old = json.loads(out.read_text()) if out.exists() else None
    if old and (old.get("ocr") or old.get("llm")) and not force:
        return c["id"], "skip", []
    is_deck = "speakerdeck.com" in c["url"]
    if is_deck and not (CACHE / f"{c['id']}.ocr.txt").exists():
        return c["id"], "書き起こしなし", []
    if not body(c["id"]):
        return c["id"], "本文なし", []
    allowed = [k for k in TOPICS if not k.startswith(("tech-", "kag-"))]
    prev = ""
    if old and old.get("facts"):
        prev = ("\n# 前回の抽出（不完全な本文から作ったもの）\n正しいものは残し、誤り・重複は直し、足りないものを足して、全体を作り直します。\n"
                + "\n".join(f"- {f['text']}" for f in old["facts"]) + "\n")
    what = ("AWS re:Invent の参加者が公開した登壇資料（Speaker Deck）を、スライド画像から書き起こしたもの" if is_deck
            else "日本語の記事（ブログ・旅行情報・解説）。re:Invent の参加者に役立つ一般的な知識（海外旅行・機内・ラスベガスの店など）を含む")
    d = ask(DECKS.format(what=what, published=c.get("published") or "不明", previous=prev, topics=topics_list(allowed)), body(c["id"]))
    facts = clean(d.get("facts", []), allowed)
    ey = d.get("event_year") or 0
    py = int((c.get("published") or "0")[:4] or 0)
    if py and ey > py:
        ey = py
    rec = {"id": c["id"], "url": c["url"], "title": c["title"], "site": c.get("site", "SpeakerDeck"), "author": c.get("author", ""),
           "published": c.get("published", ""), "retrieved": datetime.date.today().isoformat(), "event_year": ey,
           "usefulness": d.get("usefulness", 0), "summary": d.get("summary", ""), ("ocr" if is_deck else "llm"): True,
           "facts": [{**f, "year": ey} for f in facts]}
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    # 記事の年と違う年の事実は、check_extracted.py が使う付け替え表へ（事実の書き出し30字で引く）
    other = [(f["text"][:30], f["year"]) for f in facts if f["year"] and f["year"] != ey]
    return c["id"], f"{len(facts)}件（前回 {len(old['facts']) if old else 0}件）", other


def do_kag(r, force):
    out = ROOT / "sources" / "kag" / f"{r['id']}.json"
    if out.exists() and not force:
        return r["id"], "skip"
    text = body(r["id"])
    if not text:
        return r["id"], "本文なし"
    kind = {"note": "公式 note の記事", "qiita": "Qiita Organization の技術記事", "video": "YouTube 動画の題名と説明文",
            "site": "公式サイトのページ"}[r["kind"]]
    keys = ["about-minorun-kag", "kag-activities", "kag-tech"]
    d = ask(KAGP.format(kind=kind, published=r.get("published") or "なし", topics=topics_list(keys)), text)
    py = int((r.get("published") or "2026")[:4])
    facts = clean(d.get("facts", []), keys)
    for f in facts:
        f["year"] = min(f["year"], py) if f["year"] else py
    rec = {"id": r["id"], "url": r["url"], "title": r["title"], "site": r["site"], "author": r["author"],
           "published": r.get("published", ""), "retrieved": datetime.date.today().isoformat(), "source_type": "kag",
           "kind": r["kind"], "event_year": py, "usefulness": d.get("usefulness", 0), "summary": d.get("summary", ""),
           "facts": facts}
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    return r["id"], f"{len(facts)}件"


def main():
    mode = sys.argv[1]
    force = "--force" in sys.argv
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2024-01-01"
    ids = [a for a in sys.argv[2:] if not a.startswith("--") and a != since]
    if mode == "own":
        rows = [json.loads(l) for l in (ROOT / "sources/own/index.jsonl").read_text().splitlines() if l.strip()]
        rows = [r for r in rows if not ids or r["id"] in ids]
        with cf.ThreadPoolExecutor(6) as ex:
            for i, msg in ex.map(lambda r: _safe(do_own, r, force), rows):
                print(i, msg, flush=True)
        return
    if mode == "kag":
        rows = [json.loads(l) for l in (ROOT / "sources/kag/index.jsonl").read_text().splitlines() if l.strip()]
        rows = [r for r in rows if not ids or r["id"] in ids]
        with cf.ThreadPoolExecutor(6) as ex:
            for i, msg in ex.map(lambda r: _safe(do_kag, r, force), rows):
                print(i, msg, flush=True)
        return
    cand = [json.loads(l) for l in (ROOT / "sources/candidates.jsonl").read_text().splitlines() if l.strip()]
    if mode == "articles":
        rows = [c for c in cand if c["id"] in ids]
    else:
        rows = [c for c in cand if "speakerdeck.com" in c["url"] and (c["id"] in ids if ids else (c.get("published") or "") >= since)]
    ov = json.loads(OVERRIDES.read_text())
    done = []
    with cf.ThreadPoolExecutor(6) as ex:
        for res in ex.map(lambda c: _safe(do_deck, c, force), rows):
            i, msg, other = res if len(res) == 3 else (*res, [])
            print(i, msg, flush=True)
            if msg[0].isdigit():
                done.append(i)
            for k, y in other:
                ov[k] = y
    OVERRIDES.write_text(json.dumps(ov, ensure_ascii=False, indent=1) + "\n")
    listed = set(BATCH.read_text().split()) if BATCH.exists() else set()
    BATCH.write_text("\n".join(sorted(listed | set(done))) + "\n")


def _safe(fn, r, force):
    try:
        return fn(r, force)
    except Exception as e:
        return r["id"], f"失敗 {type(e).__name__}: {str(e)[:200]}"


if __name__ == "__main__":
    main()
