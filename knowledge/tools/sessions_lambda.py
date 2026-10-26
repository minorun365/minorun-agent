"""セッションカタログの控えを S3 で取り直す Lambda（fetch_sessions.py の取得部分をそのまま使う）。

11月は1時間ごと、会期中（日本時間 11/29〜12/6）は15分ごと。起動は15分おきで、会期外は毎時0〜14分の回だけ動く。
S3 に置くもの:
  sessions/latest.json                 Runtime が会話の開始時に読む控え（全件）
  sessions/changes/<時刻>.json         前回からの追加・削除・日時や部屋の変更（変更があった回だけ）
"""

import datetime
import json
import os

import boto3

from fetch_sessions import PAGE, diff, headers, page, tidy

BUCKET = os.environ["BUCKET"]
KEY = "sessions/latest.json"
JST = datetime.timezone(datetime.timedelta(hours=9))
CONFERENCE = (datetime.date(2026, 11, 29), datetime.date(2026, 12, 6))
s3 = boto3.client("s3")


def due(now):
    if CONFERENCE[0] <= now.date() <= CONFERENCE[1]:
        return True
    return now.minute < 15


def fetch_all():
    h = headers()
    total, items = page(h, 0)
    while len(items) < total:
        _, more = page(h, len(items))
        if not more:
            break
        items += more
    sessions = sorted((tidy(i) for i in items), key=lambda s: s["code"] or "")
    if len(sessions) < total:
        raise RuntimeError(f"取得が途中で止まった：{len(sessions)} / {total} 件")
    return total, sessions


def handler(event, context):
    now = datetime.datetime.now(JST)
    if not event.get("force") and not due(now):
        return {"skipped": True}
    total, sessions = fetch_all()
    try:
        old = json.loads(s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read())["sessions"]
    except s3.exceptions.NoSuchKey:
        old = None
    snap = {"retrieved": now.isoformat(timespec="seconds"), "source": PAGE, "total": total, "sessions": sessions}
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=json.dumps(snap, ensure_ascii=False).encode(),
                  ContentType="application/json")
    result = {"total": total}
    if old is not None:
        ch = diff(old, sessions)
        result.update({k: len(v) for k, v in ch.items()})
        if any(ch.values()):
            s3.put_object(Bucket=BUCKET, Key=f"sessions/changes/{now:%Y-%m-%dT%H%M}.json",
                          Body=json.dumps({"retrieved": snap["retrieved"], **ch}, ensure_ascii=False).encode(),
                          ContentType="application/json")
    print(json.dumps(result))
    return result
