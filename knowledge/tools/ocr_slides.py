"""Speaker Deck のスライド画像を読んで、1枚ずつ中身を書き起こす（sources/cache/<id>.ocr.txt）。

Speaker Deck 自身の書き起こしは、図・表・画像に埋め込まれた文字を拾えないことが多い。そこで画像そのものを
Haiku 5.5 に見せ、文字・図・表・写真の様子を日本語で書き起こさせる。5枚ずつ処理し、出力が切れた場合はさらに分割する。
材料は sources/cache/<id>.deck.json（tools/find_decks.py・tools/fetch_own.py が作る）。作り直すときは --force。
使い方: uv run --with boto3 python tools/ocr_slides.py <id>... | --all [--since 2024-01-01] [--bedrock（AWS_PROFILE=sandbox で）]
ブラウザでスライド画像を保存した場合や、保存したPDFを画像化した場合は --local-only を付ける。
画像は sources/cache/<id>.slides/slide-01.jpg などページ順でソートできる名前にする。
--local-only は元資料と画像の枚数が一致した場合だけ処理し、外部画像を取得しない。
書き起こすのは Claude Code（2026-11-22 から）。1回目は画像を knowledge/work/ocr/<id>/ に保存し、10枚ずつの頼みごとを書き出す。
答えを書いてから同じコマンドをもう一度流すと取り込む（tools/handoff.py）。--bedrock を付けると、これまでどおり Bedrock の Haiku 5.5 に見せる。
"""
import concurrent.futures as cf, json, re, shutil, sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff
import speakerdeck

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "sources" / "cache"
MODEL = "global.anthropic.claude-haiku-5-5"
BATCH, MAX_SLIDES = 5, 120
BATCH_CC = 10  # Claude Code へ頼むときの1件の枚数
PROMPT = """これは技術イベントの登壇資料のスライド画像 {first}〜{last} 枚目です。1枚ずつ、書かれている内容をすべて日本語で書き起こしてください。
- 本文・箇条書き・図の中の文字・表の値・注記・URL を漏らさず書く。英語の文字はそのまま書いてよい
- 図や表は、何と何がどうつながる・比べているかを文で書く
- 写真だけのスライドは、写っている場所や様子を1文で書く（人の顔や名前の特徴は書かない）
- 推測で補わない。読めない部分は（判読不可）と書く
- 番号は画像内に印刷された番号ではなく、渡された画像の順番で指定範囲を使う。白紙も省略しない
出力は次の形だけにします（前置きやまとめは書かない）：
[{first}] 書き起こし
[{first_1}] 書き起こし
…"""

bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")


def read_batch(urls, first):
    content = [{"text": PROMPT.format(first=first, last=first + len(urls) - 1, first_1=first + 1)}]
    for u in urls:
        data = u.read_bytes() if isinstance(u, Path) else speakerdeck.get(u, binary=True)
        content.append({"image": {"format": "jpeg", "source": {"bytes": data}}})
    r = bedrock.converse(modelId=MODEL, messages=[{"role": "user", "content": content}],
                         inferenceConfig={"maxTokens": 8000},
                         additionalModelRequestFields={"output_config": {"effort": "low"}})
    text = "".join(c.get("text", "") for c in r["output"]["message"]["content"]).strip()
    pages = list(map(int, re.findall(r"^\[(\d+)\]", text, re.M)))
    if r.get("stopReason") == "max_tokens" or pages != list(range(first, first + len(urls))):
        if len(urls) == 1:
            raise ValueError(f"{first}枚目の書き起こしが不完全（終了理由: {r.get('stopReason')}）")
        mid = len(urls) // 2
        return read_batch(urls[:mid], first) + "\n" + read_batch(urls[mid:], first + mid)
    return text


def read_batch_cc(i, urls, first):
    """Claude Code に画像を読んでもらう。画像は手元に保存してパスを渡す"""
    key, last = f"{i}-{first:03d}", first + len(urls) - 1
    local = []
    for n, u in enumerate(urls, first):
        if isinstance(u, Path):
            local.append(u.resolve())
            continue
        p = handoff.WORK / "ocr" / i / f"slide-{n:03d}.jpg"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(speakerdeck.get(u, binary=True))
        local.append(p)
    text = handoff.ask("ocr", key, PROMPT.format(first=first, last=last, first_1=first + 1),
                       f"資料 {i} の {first}〜{last} 枚目（画像は上の順）", fmt="txt", images=local).strip()
    pages = list(map(int, re.findall(r"^\[(\d+)\]", text, re.M)))
    if pages != list(range(first, last + 1)):
        raise ValueError(f"{key} の答えの番号が {first}〜{last} とそろっていない（答えを書き直す）")
    return key, text


def ocr(i, force=False):
    out = CACHE / f"{i}.ocr.txt"
    src = CACHE / f"{i}.deck.json"
    if (out.exists() and not force) or not src.exists():
        return i, "skip"
    d = json.loads(src.read_text())
    if "--local-only" in sys.argv:
        imgs = sorted((CACHE / f"{i}.slides").glob("slide-*.jpg"))
        if len(imgs) != len(d["images"]):
            raise ValueError(f"ローカル画像 {len(imgs)}枚 / 元資料 {len(d['images'])}枚")
    else:
        imgs = d["images"][:MAX_SLIDES]
    if not imgs:
        return i, "画像なし"
    parts = [f"# {d['title']}\n{d['description']}\n公開日: {d['published']}\n作者: {d['author']}\n"]
    if handoff.bedrock_mode():
        for k in range(0, len(imgs), BATCH):
            parts.append(read_batch(imgs[k:k + BATCH], k + 1))
    else:  # 全部の頼みごとを書き出してから待つ（1件ずつ往復しない）
        keys, waiting = [], []
        for k in range(0, len(imgs), BATCH_CC):
            try:
                key, text = read_batch_cc(i, imgs[k:k + BATCH_CC], k + 1)
                keys.append(key)
                parts.append(text)
            except handoff.Pending as p:
                waiting.append(p.task)
        if waiting:
            raise handoff.Pending(waiting)
    out.write_text("\n".join(parts) + "\n")
    if not handoff.bedrock_mode():
        for key in keys:
            handoff.done("ocr", key, "txt")
        shutil.rmtree(handoff.WORK / "ocr" / i, ignore_errors=True)
    return i, f"{len(imgs)}枚"


def main():
    force = "--force" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--all" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2024-01-01"
        args = [p.name[:-10] for p in sorted(CACHE.glob("*.deck.json"))
                if json.loads(p.read_text()).get("published", "") >= since]
        args = [a for a in args if a not in sys.argv]
    done, pending = 0, []
    with cf.ThreadPoolExecutor(6) as ex:
        for i, msg in ex.map(lambda x: _safe(x, force), args):
            if isinstance(msg, list):
                pending += msg
                msg = f"頼みごと {len(msg)}件"
            done += msg != "skip" and not msg.startswith("頼みごと")
            print(i, msg, flush=True)
    print(f"{len(args)}件中 {done}件を処理")
    handoff.report("ocr", pending)


def _safe(i, force):
    try:
        return ocr(i, force)
    except handoff.Pending as p:
        return i, [str(t) for t in p.task]
    except Exception as e:
        return i, f"失敗 {type(e).__name__}: {e}"


if __name__ == "__main__":
    main()
