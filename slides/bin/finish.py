"""Harness のエージェントが使う：書き上げた deck.md と図（fig/*.png）を、スライド作成機能が受け取る置き場（S3 の work/<ジョブ>/）へ上げる。

使い方: python3 /app/bin/finish.py <作業フォルダ>   フォルダ名がジョブの番号
"""
import os
import sys
from pathlib import Path

import boto3

d = Path(sys.argv[1]).resolve()
job, bucket = d.name, os.environ["SLIDES_BUCKET"]
s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-2"))
files = [d / "deck.md"] + sorted(p for p in (d / "fig").glob("*.png") if not p.name.endswith(".clean.png"))[:8]
for f in files:
    if f.exists() and f.stat().st_size < 5_000_000:
        s3.upload_file(str(f), bucket, f"work/{job}/{f.relative_to(d)}")
print(f"finished {len(files)} files")
