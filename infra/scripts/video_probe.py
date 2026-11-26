"""資料から解説動画を作る機能を、画面を通さずに本番で1本通す（2026-11-26）。

資料を一時置き場の doc/probe/ へ置き、起こし役の Lambda を呼んで、v/<ジョブ>/status.json が done か error になるまで見る。
回数は数えない（counted を渡さない）。使い方：
  AWS_PROFILE=sandbox uv run --with boto3 python infra/scripts/video_probe.py <資料のパス> [--name 表示名]
"""

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

import boto3

REGION = "us-west-2"
ACCOUNT = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
UPLOADS = f"minorun-agent-uploads-{ACCOUNT}"
VIDEOS = f"minorun-agent-videos-{ACCOUNT}"
LAUNCHER = "minorun-agent-video-launch"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--name", default="")
    a = ap.parse_args()
    s3 = boto3.client("s3", region_name=REGION)
    doc = f"doc/probe/{secrets.token_hex(16)}"
    s3.upload_file(a.doc, UPLOADS, doc)
    job = "probe" + secrets.token_hex(8)
    s3.put_object(Bucket=VIDEOS, Key=f"v/{job}/status.json", ContentType="application/json", CacheControl="no-cache, max-age=0",
                  Body=json.dumps({"stage": "queued", "at": int(time.time())}).encode())
    boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=LAUNCHER, InvocationType="Event",
        Payload=json.dumps({"job": job, "doc": doc, "name": a.name or Path(a.doc).name, "user": "probe"}, ensure_ascii=False).encode())
    print(f"job={job}", flush=True)
    t0, last = time.time(), None
    while time.time() - t0 < 1800:
        st = json.loads(s3.get_object(Bucket=VIDEOS, Key=f"v/{job}/status.json")["Body"].read())
        if st.get("stage") != last:
            last = st.get("stage")
            print(f"{int(time.time() - t0):4d}s {json.dumps(st, ensure_ascii=False)}", flush=True)
        if last in ("done", "error"):
            print(f"https://agent.minoruonda.com/v/{job}/")
            sys.exit(0 if last == "done" else 1)
        time.sleep(5)
    sys.exit(2)


if __name__ == "__main__":
    main()
