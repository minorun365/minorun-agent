"""質問に添えられた画像。

画面が縮めた JPEG を一時置き場（S3）へ直接送り、質問には置き場の場所だけが届く。
ここでは読んで形を確かめ、読んだ直後に置き場から消す。画像は会話の入れ物の中だけで持ち、
会話の控え（Memory）と1問1行の記録には印の文だけを残す。
"""

import json
import re

import boto3

from settings import REGION, UPLOAD_BUCKET

MAX_BYTES = 1_500_000
KEY = re.compile(r"^up/([0-9a-f]{16})/[0-9a-f]{32}\.jpg$")
DEFAULT_QUESTION = "この画像について教えてください。"
UNREADABLE = "画像を読み込めませんでした。もう一度添付して送ってください。"
# 画像を外した場所に置く印。モデルには「前に画像があったが、いまは見られない」と伝わる
PLACEHOLDER = "[利用者が画像を1枚添付した。画像は保存していないので、いまは見られない]"

_s3 = boto3.client("s3", region_name=REGION)


def owned_key(key, anon):
    """確認役が組み立てた場所だけを受け付ける（匿名IDのハッシュが本人のものと一致すること）。"""
    m = KEY.match(key or "")
    return key if m and m.group(1) == anon else None


def load(key):
    """置き場から読み、JPEG であることと大きさを確かめて返す。読めたら置き場から消す。だめなら None。"""
    if not UPLOAD_BUCKET:
        return None
    try:
        obj = _s3.get_object(Bucket=UPLOAD_BUCKET, Key=key)
        if obj["ContentLength"] > MAX_BYTES:
            return None
        data = obj["Body"].read(MAX_BYTES + 1)
    except Exception as e:
        print(json.dumps({"image_error": type(e).__name__}))
        return None
    try:
        _s3.delete_object(Bucket=UPLOAD_BUCKET, Key=key)
    except Exception as e:
        # 消せなくても置き場の期限（1日）で消える
        print(json.dumps({"image_delete_error": type(e).__name__}))
    if len(data) > MAX_BYTES or not data.startswith(b"\xff\xd8\xff"):
        return None
    return data


def block(data):
    return {"image": {"format": "jpeg", "source": {"bytes": data}}}


def strip(messages):
    """画像を印の文に置き換えた写しを返す（元の並びは変えない）。画像が無ければ同じ並びをそのまま返す。"""
    if not any("image" in c for m in messages for c in m.get("content", [])):
        return messages
    return [{**m, "content": [{"text": PLACEHOLDER} if "image" in c else c for c in m["content"]]}
            if any("image" in c for c in m.get("content", [])) else m for m in messages]
