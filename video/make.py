"""資料1つから、ずんだもんが3分で解説する MP4 を作る（段取り役）。

  split（ページ画像と文字）→ read（図まで読む）→ story（問いから台本）→ voice（VOICEVOX）→ frames（1行1枚）→ mp4

手元で試すとき：
  ANTHROPIC_PRIMARY=off AWS_PROFILE=sandbox CHROME_PATH=... MARP_MODULES=... uv run --no-project --with boto3,python-pptx \
    python video/make.py <資料> <作業フォルダ>
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

import doc
import story as story_mod
import voice

HERE = Path(__file__).parent


def _run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {r.stderr[-600:]}")
    return r.stdout


def attach_pages(st, pages, work, figures):
    """page のコマへ、映す画像（作業フォルダからの相対パス）と縦横比を書き足す。focus を書き忘れた図のページは図へ寄る"""
    by_n = {p["n"]: p for p in pages}
    for sc in st["scenes"]:
        if sc["kind"] != "page":
            continue
        p = by_n[sc["page"]]
        sc["src"] = os.path.relpath(p["image"], work)
        w, h = Image.open(p["image"]).size
        sc["ar"] = round(w / h, 4)
        if "focus" not in sc and sc["page"] in figures:
            f = figures[sc["page"]]
            if (f[2] - f[0]) * (f[3] - f[1]) < 0.6:
                sc["focus"] = f
        # 寄った範囲の外を光らせても見えないので、光らせる範囲を寄った範囲の中へ収める
        if "focus" in sc:
            f = sc["focus"]
            for ln in sc["lines"]:
                b = ln.get("box")
                if b:
                    nb = [max(b[0], f[0]), max(b[1], f[1]), min(b[2], f[2]), min(b[3], f[3])]
                    if nb[2] - nb[0] < 0.03 or nb[3] - nb[1] < 0.03:
                        ln.pop("box")
                    else:
                        ln["box"] = nb


def frames(st, work):
    story_path = Path(work) / "story.json"
    story_path.write_text(json.dumps(st, ensure_ascii=False))
    # 台紙は作業フォルダへ写す（画像を相対パスで読むため）
    shutil.copy(HERE / "frame.html", Path(work) / "frame.html")
    out = Path(work) / "frames"
    if out.exists():
        shutil.rmtree(out)
    cmd = ["node", str(HERE / "frames.mjs"), str(story_path), str(Path(work) / "frame.html"), str(out)]
    try:
        _run(cmd, timeout=900)
    except RuntimeError:
        # Chromium の起動が時間切れになることがある。1回だけやり直す
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree("/tmp/chrome-video", ignore_errors=True)
        _run(cmd, timeout=900)
    return sorted(out.glob("f*.png"))


def mp4(st, pngs, work, dest):
    """1行1枚の絵を、その行の声の長さだけ映す。声はつないで1本にする"""
    work = Path(work)
    rows = [ln for sc in st["scenes"] for ln in sc["lines"]]
    assert len(rows) == len(pngs)
    lst = work / "frames.txt"
    with lst.open("w") as f:
        for ln, png in zip(rows, pngs):
            f.write(f"file '{png}'\nduration {ln['sec']:.3f}\n")
        f.write(f"file '{pngs[-1]}'\n")  # concat の決まり：最後の1枚をもう一度書かないと尺が欠ける
    alst = work / "audio.txt"
    alst.write_text("".join(f"file '{ln['audio']}'\n" for ln in rows))
    voice_wav = work / "voice.wav"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(alst), "-c", "copy", str(voice_wav)])
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(voice_wav),
          "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-tune", "stillimage",
          "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(dest)], timeout=1200)
    return dest


def poster(pngs, dest):
    Image.open(pngs[0]).convert("RGB").resize((1280, 720)).save(dest, "JPEG", quality=85)


def make(src, work, title_hint="", log=print):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log({"stage": "reading"})
    got = doc.split(src, work)
    pages = got["pages"]
    notes = story_mod.read_pages(pages, title_hint)
    (work / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=1))
    log({"stage": "writing", "pages": len(pages), "total": got["total"], "read_sec": round(time.time() - t0)})
    st = story_mod.write_story(pages, notes, title_hint)
    attach_pages(st, pages, work, story_mod.figure_boxes(notes))
    log({"stage": "voicing"})
    voice.start_engine()
    total = voice.voice_story(st, work / "voice")
    voice.stop_engine()
    log({"stage": "rendering", "seconds": round(total, 1), "speed": st["speed"]})
    pngs = frames(st, work)
    out = mp4(st, pngs, work, work / "video.mp4")
    poster(pngs, work / "poster.jpg")
    st["seconds"] = round(total, 1)
    st["pages"] = len(pages)
    st["total_pages"] = got["total"]  # 上限（doc.MAX_PAGES）で切ったかを画面で伝えるため
    st["usage"] = dict(story_mod.USAGE)
    (work / "story.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))
    log({"stage": "done", "pages": st["pages"], "total": st["total_pages"], "seconds": st["seconds"], "elapsed": round(time.time() - t0), "usage": st["usage"]})
    return out, st


if __name__ == "__main__":
    make(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "",
         log=lambda d: print(json.dumps(d, ensure_ascii=False), flush=True))
