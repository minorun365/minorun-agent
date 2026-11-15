"""作り済みのスライド（PPTX 保存の機能を入れる前の分）に、deck.pptx とビューアの「PPTXで保存」ボタンを足す。

PNG は工房の /tmp にしか無かったので、公開用 S3 に残っている WebP（2560×1440）から作る。
作り方とボタンは slides/handler.py の build_pptx・SAVE と同じ。
  AWS_PROFILE=sandbox uv run --no-project --with boto3 --with pillow --with python-pptx python infra/scripts/slides_pptx_backfill.py [--apply]
既定は対象を出すだけ。--apply で書き込む。
"""

import io
import json
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

import boto3
from PIL import Image
from pptx import Presentation
from pptx.util import Emu

s3 = boto3.client("s3", region_name="us-west-2")
BUCKET = "minorun-agent-slides-" + boto3.client("sts").get_caller_identity()["Account"]  # slides-stack.ts と同じ名前

OLD_CSS = ".share{flex:none;display:inline-flex;align-items:center;gap:6px;background:#0f1419;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:9px 16px;border-radius:999px}"
NEW_CSS = (".share,.save{flex:none;display:inline-flex;align-items:center;gap:6px;background:#0f1419;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:9px 16px;border-radius:999px;white-space:nowrap}\n"
           ".save{background:var(--btn);color:var(--ink);border:1px solid var(--line)}\n"
           "@media (max-width:520px){header{flex-wrap:wrap}header h1{flex-basis:100%}}")
SAVE = '<a class="save" href="deck.pptx" download title="1ページを1枚の画像にしたPowerPoint（文字は編集できません）">PPTXで保存</a>'


def pptx_name(title):
    return (re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", title).strip()[:60] or "スライド") + ".pptx"


def jobs():
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="s/", Delimiter="/")
    return [p["Prefix"] for page in pages for p in page.get("CommonPrefixes", [])]


def get(key):
    try:
        return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None


def main(apply):
    for base in jobs():
        st = get(base + "status.json")
        st = json.loads(st) if st else {}
        if st.get("stage") != "done":
            continue
        page = (get(base + "index.html") or b"").decode()
        if "deck.pptx" in page:
            print("済み", base)
            continue
        if OLD_CSS not in page or '<a class="share"' not in page:
            print("ビューアの形が想定と違うので飛ばす", base)
            continue
        n = int(st.get("pages") or 0)
        print("対象", base, n, "枚", st.get("title"))
        if not apply:
            continue
        with tempfile.TemporaryDirectory() as d:
            prs = Presentation()
            prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
            for i in range(1, n + 1):
                jpg = Path(d) / f"{i:02d}.jpg"
                Image.open(io.BytesIO(get(f"{base}{i:02d}.webp"))).convert("RGB").save(jpg, "JPEG", quality=92, subsampling=0)
                prs.slides.add_slide(prs.slide_layouts[6]).shapes.add_picture(str(jpg), 0, 0, prs.slide_width, prs.slide_height)
            out = Path(d) / "deck.pptx"
            prs.save(out)
            s3.upload_file(str(out), BUCKET, base + "deck.pptx", ExtraArgs={
                "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "ContentDisposition": "attachment; filename=\"slides.pptx\"; filename*=UTF-8''" + urllib.parse.quote(pptx_name(st.get("title") or "スライド")),
                "CacheControl": "public, max-age=31536000, immutable"})
            print("  pptx", round(out.stat().st_size / 1e6, 1), "MB")
        page = page.replace(OLD_CSS, NEW_CSS).replace('<a class="share"', SAVE + '<a class="share"', 1)
        s3.put_object(Bucket=BUCKET, Key=base + "index.html", Body=page.encode(),
                      ContentType="text/html; charset=utf-8", CacheControl="public, max-age=300")


if __name__ == "__main__":
    main("--apply" in sys.argv)
