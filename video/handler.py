"""動画作成の1ジョブ（入口は app.py の AgentCore Runtime）。

受け取るもの：{"job", "doc": 一時置き場の鍵, "name": 資料の名前, "user", "counted": [数えた回数の鍵]}
進み具合は公開用 S3 の v/<ジョブ>/status.json に書き、画面が数秒おきに読みに来る。
  queued → reading → writing → voicing → rendering → done / error
資料そのものは読んだらすぐ一時置き場から消し、作業フォルダごと捨てる。残すのは動画と表紙の絵と台本の字幕だけ。
"""

import json
import os
import shutil
import time
import traceback
from pathlib import Path

import boto3

import doc
import make
import viewer

REGION = os.environ.get("AWS_REGION", "us-west-2")
BUCKET = os.environ.get("VIDEO_BUCKET", "")
UPLOADS = os.environ.get("UPLOAD_BUCKET", "")
TABLE = os.environ.get("VIDEO_TABLE", "")
# 動画のページの下に置くもの（qa：3つの問いと答え、captions：字幕の全文。カンマ区切り。空なら動画だけ）
VIEWER_PARTS = tuple(filter(None, os.environ.get("VIDEO_VIEWER_PARTS", "qa").split(",")))
s3 = boto3.client("s3", region_name=REGION)

STAGE_SAY = {"reading": "資料を読んでいます", "writing": "台本を書いています", "voicing": "ずんだもんが読み上げを録っています",
             "rendering": "動画にまとめています"}


def status(job, stage, **extra):
    body = json.dumps({"stage": stage, "at": int(time.time()), **({"say": STAGE_SAY[stage]} if stage in STAGE_SAY else {}),
                       **extra}, ensure_ascii=False)
    s3.put_object(Bucket=BUCKET, Key=f"v/{job}/status.json", Body=body.encode(),
                  ContentType="application/json", CacheControl="no-cache, max-age=0")


def _refund(counted):
    if not (TABLE and counted):
        return
    ddb = boto3.client("dynamodb", region_name=REGION)
    for pk in counted:
        try:
            ddb.update_item(TableName=TABLE, Key={"pk": {"S": pk}}, UpdateExpression="ADD n :minus",
                            ConditionExpression="n > :zero",
                            ExpressionAttributeValues={":minus": {"N": "-1"}, ":zero": {"N": "0"}})
        except Exception as e:
            print(json.dumps({"video_refund_error": type(e).__name__}))


def _fetch(key, dest):
    obj = s3.get_object(Bucket=UPLOADS, Key=key)
    if obj["ContentLength"] > doc.MAX_BYTES:
        raise doc.DocError("資料が大きすぎます（40MB まで）。")
    dest.write_bytes(obj["Body"].read())
    try:
        s3.delete_object(Bucket=UPLOADS, Key=key)
    except Exception as e:  # 消せなくても置き場の期限（1日）で消える
        print(json.dumps({"video_doc_delete_error": type(e).__name__}))


def handler(event, _context=None):
    job = event["job"]
    work = Path(f"/tmp/video-{job}")
    t0 = time.time()
    try:
        status(job, "reading")
        work.mkdir(parents=True, exist_ok=True)
        src = work / "source"
        _fetch(event["doc"], src)
        name = " ".join(str(event.get("name") or "").split())[:80]

        def log(d):
            print(json.dumps({"job": job, **d}, ensure_ascii=False))
            if d.get("stage") in STAGE_SAY:
                status(job, d["stage"], **({"pages": d["pages"]} if "pages" in d else {}))

        out, st = make.make(src, work, name, log=log)
        for local, key, ctype in ((out, "video.mp4", "video/mp4"), (work / "poster.jpg", "poster.jpg", "image/jpeg")):
            s3.upload_file(str(local), BUCKET, f"v/{job}/{key}",
                           ExtraArgs={"ContentType": ctype, "CacheControl": "public, max-age=31536000, immutable"})
        captions = [{"text": ln["text"], "sec": ln["sec"]} for sc in st["scenes"] for ln in sc["lines"]]
        meta = {"title": st["title"], "subtitle": st["subtitle"], "questions": st["questions"], "answers": st["answers"],
                "seconds": st["seconds"], "captions": captions, "name": name, "credit": "VOICEVOX:ずんだもん"}
        s3.put_object(Bucket=BUCKET, Key=f"v/{job}/meta.json", Body=json.dumps(meta, ensure_ascii=False).encode(),
                      ContentType="application/json", CacheControl="public, max-age=300")
        page = viewer.render(meta, f"{viewer.SITE}/v/{job}/", VIEWER_PARTS)
        s3.put_object(Bucket=BUCKET, Key=f"v/{job}/index.html", Body=page.encode(),
                      ContentType="text/html; charset=utf-8", CacheControl="public, max-age=300")
        # 使ったページ数と資料全体のページ数。上限で切ったときは画面の完成の札で「先頭◯ページから作った」と伝える
        status(job, "done", title=st["title"], seconds=st["seconds"], pages=st["pages"], total=st["total_pages"])
        print(json.dumps({"job": job, "ok": True, "pages": st["pages"], "total": st["total_pages"], "seconds": st["seconds"], "speed": st["speed"],
                          "elapsed": round(time.time() - t0), "usage": st["usage"]}))
    except doc.DocError as e:
        status(job, "error", message=f"{e} 今日の回数には数えません。")
        _refund(event.get("counted"))
        print(json.dumps({"job": job, "ok": False, "error": "doc", "detail": str(e)}, ensure_ascii=False))
    except Exception as e:
        status(job, "error", message="動画を作れませんでした。今日の回数には数えないので、少し時間をおいてもう一度送ってください。")
        _refund(event.get("counted"))
        print(json.dumps({"job": job, "ok": False, "error": type(e).__name__, "detail": str(e)[:500],
                          "trace": traceback.format_exc()[-1500:]}))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            s3.delete_object(Bucket=UPLOADS, Key=event.get("doc", ""))
        except Exception:
            pass
