"""動画作成機能の起こし役（確認役から非同期で呼ばれる。2026-11-26）。

確認役は8秒で返すので、入れ物の立ち上がり（20秒前後）を待てない。ここでジョブごとのセッションで動画作成機能を起こし、
動画作成機能が「受け付けた」と返したら終わる。起こせなかったら画面へ失敗を出し、数えた回数を戻す。
"""

import json
import os
import secrets
import time

import boto3
from botocore.config import Config

RUNTIME = os.environ["VIDEO_RUNTIME"]
BUCKET = os.environ["VIDEO_BUCKET"]
TABLE = os.environ["VIDEO_TABLE"]
agentcore = boto3.client("bedrock-agentcore", config=Config(read_timeout=240, retries={"max_attempts": 2}))
s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")


def _fail(job, counted):
    s3.put_object(Bucket=BUCKET, Key=f"v/{job}/status.json", ContentType="application/json", CacheControl="no-cache, max-age=0",
                  Body=json.dumps({"stage": "error", "at": int(time.time()),
                                   "message": "動画の作成を始められませんでした。今日の回数には数えないので、少し時間をおいてもう一度送ってください。"},
                                  ensure_ascii=False).encode())
    for pk in counted or []:
        try:
            ddb.update_item(TableName=TABLE, Key={"pk": {"S": pk}}, UpdateExpression="ADD n :minus", ConditionExpression="n > :zero",
                            ExpressionAttributeValues={":minus": {"N": "-1"}, ":zero": {"N": "0"}})
        except Exception as e:
            print(json.dumps({"video_refund_error": type(e).__name__}))


def handler(event, _context=None):
    job = event["job"]
    try:
        # セッション ID は33字以上。1本＝1つの入れ物
        res = agentcore.invoke_agent_runtime(
            agentRuntimeArn=RUNTIME, qualifier="DEFAULT", runtimeSessionId=f"video-job-{job}-{secrets.token_hex(8)}",
            contentType="application/json", accept="application/json",
            payload=json.dumps(event, ensure_ascii=False).encode())
        got = json.loads(res["response"].read() or b"{}")
        if not got.get("accepted"):
            raise RuntimeError(f"not accepted: {got}")
        print(json.dumps({"video_launch": job, "ok": True}))
    except Exception as e:
        print(json.dumps({"video_launch": job, "ok": False, "error": type(e).__name__, "detail": str(e)[:300]}))
        _fail(job, event.get("counted"))
