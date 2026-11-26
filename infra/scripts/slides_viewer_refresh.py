"""作成済みのスライドのビューア（s/<番号>/index.html）の <style>・<footer>・<script> を、いまの slides/handler.py の版へ差し替える。

ビューアは作ったときの HTML を S3 に置くので、handler.py の VIEWER を直しても既存のページには届かない。
題・ボタン・OGP は残し、足元は今のページの出典のリンクと種類（記事か調べものか）から作り直す。
既定は件数を数えるだけ。--apply で書き込む。

実行：AWS_PROFILE=sandbox uv run --with-requirements slides/requirements.txt python infra/scripts/slides_viewer_refresh.py [--apply]
"""
import html
import os
import re
import sys
from pathlib import Path

import boto3

BUCKET = os.environ.get("SLIDES_BUCKET") or \
    "minorun-agent-slides-" + boto3.client("sts").get_caller_identity()["Account"]
os.environ.setdefault("SLIDES_BUCKET", BUCKET)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "slides"))
import handler  # noqa: E402

PARTS = {name: re.compile(rf"<{name}>[\s\S]*?</{name}>") for name in ("style", "footer", "script")}


def rebuilt(body, pages):
    footer = PARTS["footer"].search(body).group(0)
    hrefs = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', footer)]
    sources = [h for h in hrefs if not h.startswith(handler.SITE)]
    handler.KIND["value"] = "topic" if ("出典" in footer and "元の記事" not in footer) else "article"
    if handler.KIND["value"] == "article" and not sources:
        return None
    page = handler.VIEWER.format(title="t", pages=pages, url="u/", og="u/og.jpg", site="s", save="",
                                 **handler.viewer_texts("t", "u/", sources))
    for name, rx in PARTS.items():
        new = rx.search(page).group(0)
        body = rx.sub(lambda _: new, body, count=1)
    return body


def main(apply):
    s3 = boto3.client("s3", region_name="us-west-2")
    keys = [o["Key"] for p in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="s/")
            for o in p.get("Contents", []) if o["Key"].endswith("/index.html")]
    done = same = skipped = 0
    for key in keys:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
        m = re.search(r"const N=(\d+);", body)
        new = rebuilt(body, int(m.group(1))) if m and all(len(rx.findall(body)) == 1 for rx in PARTS.values()) else None
        if new is None:
            print("skip", key)
            skipped += 1
            continue
        if new == body:
            same += 1
            continue
        done += 1
        if apply:
            s3.put_object(Bucket=BUCKET, Key=key, Body=new.encode(),
                          ContentType="text/html; charset=utf-8", CacheControl="public, max-age=300")
    print(f"全{len(keys)}本：差し替え{done}本（{'書き込み済み' if apply else '未書き込み'}）・最新{same}本・対象外{skipped}本")


if __name__ == "__main__":
    main("--apply" in sys.argv)
