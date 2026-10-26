"""現地からの速報を足す・一覧する・消す（エージェントのツール get_announcements が読む）。

  uv run --with boto3 python knowledge/tools/announce.py add "Venetian のバッジ受け取りが30分待ち" [--url https://...]
  uv run --with boto3 python knowledge/tools/announce.py list
  uv run --with boto3 python knowledge/tools/announce.py remove <番号>
置き場は S3 の announcements/latest.json。ナレッジベースの取り込みは要らず、1分以内にエージェントへ届く。
"""
import argparse
import datetime
import json

import boto3

session = boto3.Session(region_name="us-west-2")
s3 = session.client("s3")
BUCKET = f"minorun-agent-knowledge-{session.client('sts').get_caller_identity()['Account']}"
KEY = "announcements/latest.json"
JST = datetime.timezone(datetime.timedelta(hours=9))


def load():
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {"items": []}


def save(data):
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=json.dumps(data, ensure_ascii=False, indent=1).encode(),
                  ContentType="application/json")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("text")
    a.add_argument("--url", default="")
    sub.add_parser("list")
    r = sub.add_parser("remove")
    r.add_argument("index", type=int)
    args = ap.parse_args()
    data = load()
    if args.cmd == "add":
        item = {"at": datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M"), "text": args.text}
        if args.url:
            item["url"] = args.url
        data["items"].append(item)
        save(data)
    elif args.cmd == "remove":
        data["items"].pop(args.index)
        save(data)
    for i, it in enumerate(data["items"]):
        print(i, it["at"], it["text"], it.get("url", ""))


if __name__ == "__main__":
    main()
