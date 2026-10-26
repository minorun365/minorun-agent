"""利用停止の一覧・追加・解除。利用者は監視ページと同じ書き方（A-匿名ブラウザ / G-Googleログイン ＋16桁）で指定する。

  uv run --with boto3 python infra/scripts/ban.py list
  uv run --with boto3 python infra/scripts/ban.py add A-0123456789abcdef "理由"
  uv run --with boto3 python infra/scripts/ban.py remove A-0123456789abcdef

停止の行は回数の表（minorun-agent-usage）の pk=ban#a#… / ban#u#… に置き、期限を付けない。
入口の確認役がこの行を見て、評価・先読みを含むすべての要求を 403 で断る。
同じ人の匿名IDとログインは確認役がつなげて両方を止めるので、解除するときは関連する停止も消し、累積点数をリセットする。
"""

import datetime
import re
import sys
import uuid

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
    pk = _pk(sys.argv[2])
    items, kwargs = [], {"FilterExpression": "begins_with(pk, :p)", "ExpressionAttributeValues": {":p": "ban#"}, "ConsistentRead": True}
    while True:
        page = TABLE.scan(**kwargs)
        items.extend(page["Items"])
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    targets = {pk}
    while True:
        previous = set(targets)
        decisive = {(r.get("conv_pk"), r.get("conv_sk")) for r in items if r["pk"] in targets and r.get("conv_pk") and r.get("conv_sk")}
        for row in items:
            if row.get("linked_from") in targets or (row.get("conv_pk"), row.get("conv_sk")) in decisive:
                targets.add(row["pk"])
            if row["pk"] in targets and row.get("linked_from"):
                targets.add(row["linked_from"])
        if targets == previous:
            break
    if len(targets) > 50:
        sys.exit("関連する停止IDが50件を超えるため、一括解除の対象を確認してください")
    now = datetime.datetime.now(JST).isoformat(timespec="milliseconds")
    epoch = str(uuid.uuid4())
    actions = []
    for target in sorted(targets):
        actions.extend([
            {"Delete": {"TableName": TABLE.name, "Key": {"pk": target}}},
            {"Put": {"TableName": TABLE.name, "Item": {
                "pk": "moderation-reset#"+target[4:], "epoch": epoch, "cutoff": now}}},
        ])
    TABLE.meta.client.transact_write_items(TransactItems=actions)
    print("解除した", sys.argv[2])
else:
    sys.exit(__doc__)
