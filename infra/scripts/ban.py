"""利用停止の一覧・追加・解除。利用者は監視ページと同じ書き方（A-匿名ブラウザ / G-Googleログイン ＋16桁）で指定する。

  uv run --with boto3 python infra/scripts/ban.py list
  uv run --with boto3 python infra/scripts/ban.py add A-0123456789abcdef "理由"
  uv run --with boto3 python infra/scripts/ban.py remove A-0123456789abcdef

停止の行は回数の表（minorun-agent-usage）の pk=ban#a#… / ban#u#… に置き、期限を付けない。
入口の確認役がこの行を見て、評価・先読みを含むすべての要求を 403 で断る。
同じ人の匿名IDとログインは確認役がつなげて両方を止めるので、解除するときは list で linked の行も消す。
"""

import datetime
import re
import sys

import boto3

TABLE = boto3.Session(region_name="us-west-2").resource("dynamodb").Table("minorun-agent-usage")
JST = datetime.timezone(datetime.timedelta(hours=9))


def _pk(ident):
    m = re.fullmatch(r"([AG])-([0-9a-f]{16})", ident)
    if not m:
        sys.exit("利用者は A-＋16桁 か G-＋16桁 で指定する")
    return f"ban#{'a' if m[1] == 'A' else 'u'}#{m[2]}"


def _ident(pk):
    kind, value = pk.split("#")[1:]
    return ("A-" if kind == "a" else "G-") + value


cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
if cmd == "list":
    items, kwargs = [], {"FilterExpression": "begins_with(pk, :p)", "ExpressionAttributeValues": {":p": "ban#"}}
    while True:
        page = TABLE.scan(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    for i in sorted(items, key=lambda i: i.get("at", "")):
        print(_ident(i["pk"]), i.get("at", ""), i.get("source", ""), i.get("reason") or i.get("linked_from", ""))
    print(f"{len(items)}件")
elif cmd == "add" and len(sys.argv) >= 3:
    TABLE.put_item(Item={"pk": _pk(sys.argv[2]), "source": "manual", "reason": sys.argv[3] if len(sys.argv) > 3 else "",
                         "at": datetime.datetime.now(JST).isoformat(timespec="seconds")})
    print("停止した", sys.argv[2])
elif cmd == "remove" and len(sys.argv) >= 3:
    TABLE.delete_item(Key={"pk": _pk(sys.argv[2])})
    print("解除した", sys.argv[2])
else:
    sys.exit(__doc__)
