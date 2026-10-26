"""質問の分類・記録・通知。分類設定は同梱する system.local.txt から読む。"""
from pathlib import Path

import datetime
import json
import os
import re
import time
from urllib.parse import urlencode

import boto3

MODEL = os.environ["MODEL_ID"]
TABLE = os.environ["LOG_TABLE"]
STATE_TABLE = os.environ["STATE_TABLE"]
TOPIC = os.environ["TOPIC_ARN"]
FLAGS = ("normal", "off_topic", "injection", "personal_info", "bug_report", "abuse")
ALERT = ("injection", "personal_info", "abuse")
QUIET = 15 * 60
JST = datetime.timezone(datetime.timedelta(hours=9))

bedrock = boto3.client("bedrock-runtime")
ddb = boto3.client("dynamodb")
sns = boto3.client("sns")

SYSTEM = (Path(__file__).with_name("system.local.txt").read_text().strip() if Path(__file__).with_name("system.local.txt").exists() else "")


def classify(question, answer):
    if not SYSTEM:
        raise RuntimeError("system.local.txt を設定してください")
    text = f"<question>{question[:2000]}</question>\n<answer_head>{(answer or '')[:300]}</answer_head>"
    res = bedrock.converse(
        modelId=MODEL, system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": [{"text": text}]}],
        # 考える分にもトークンを使うので、上限は本文より十分大きくする（少ないと本文が空で返る）
        inferenceConfig={"maxTokens": 2000},
        additionalModelRequestFields={"output_config": {"effort": "low"}},
    )
    raw = "".join(b.get("text", "") for b in res["output"]["message"]["content"])
    for m in reversed(re.findall(r"\{[^{}]*\}", raw)):
        try:
            data = json.loads(m)
        except ValueError:
            continue
        if data.get("flag") in FLAGS:
            return data["flag"], str(data.get("reason") or "")[:30]
    return "normal", "判定できず（応答の形が不正）"


def _detail(entry):
    """待機中は行のキーだけを持ち、送信時に保存済みの全文を読む。"""
    if "keys" not in entry:
        return f"{entry['at']}  {entry['flag']}\n質問（旧形式・先頭80字）：{entry['q']}"
    image = ddb.get_item(TableName=TABLE, Key=entry["keys"], ConsistentRead=True).get("Item", {})
    def value(name, default=""):
        return image.get(name, {}).get("S", default)
    if not image:
        raise RuntimeError("通知対象の会話が取得できません")
    at = datetime.datetime.fromisoformat(value("created_at")).astimezone(JST)
    weekday = "月火水木金土日"[at.weekday()]
    actor = "user-" + value("user") if value("user") else value("anon")
    google = actor.startswith("user-")
    identifier = ("G-" + actor[5:] if google else "A-" + actor) if actor else "不明"
    kind = "Googleログイン" if google else "匿名ブラウザ" if actor else "記録なし"
    via = {"typed": "自由入力", "ai": "AIの質問候補", "starter": "用意した質問", "link": "質問リンク"}.get(value("via"), "記録なし")
    flag = value("flag", entry["flag"])
    label = {"injection": "指示や設定の書き換えを狙う入力", "personal_info": "個人情報を含む入力",
             "abuse": "良識を逸脱した入力（利用停止済み。解除は infra/scripts/ban.py）"}.get(flag, flag)
    query = {"v": "list", "d": at.date().isoformat()}
    if actor:
        query["u"] = identifier
    url = os.environ.get("ADMIN_BASE_URL", "https://agent.minoruonda.com/admin/") + "?" + urlencode(query)
    tools = "、".join(t.get("S", "") for t in image.get("tools", {}).get("L", [])) or "なし"
    return (
        f"日時：{at:%Y/%m/%d}（{weekday}）{at:%H:%M:%S} JST\n"
        f"判定：{label}（{flag}）\n判定理由：{value('flag_reason', entry.get('reason', '記録なし'))}\n"
        f"利用者：{identifier}（{kind}）\n質問の出どころ：{via}\n"
        f"会話ID：{value('pk', '記録なし')}\n質問ID：{value('tid', '記録なし')}\n"
        f"使った道具：{tools}\n\n"
        f"質問：\n{value('question', '記録なし')}\n\n"
        f"回答：\n{value('answer') or '回答の記録なし'}\n\n"
        f"同じ日の利用者の質問一覧：\n{url}"
    )


def _notify(entries):
    # 全件を取得できてから送る。長文も切り捨てず分割し、SNS のサイズ上限に収める。
    details = [_detail(e) for e in entries]
    # 利用停止を含む回は件名で分かるようにする（ダッシュボードの「利用停止」の欄にも出る）
    subject = "みのるんエージェント｜利用停止" if any(e.get("flag") == "abuse" for e in entries) else "みのるんエージェント｜要注意の入力"
    body = (f"みのるんエージェントに要注意の入力がありました（{len(entries)}件）。\n"
            "判定はAIによるものです。質問・回答は保存済みの伏せ字を含みます。\n\n" +
            "\n\n====================\n\n".join(details))
    # 4バイトの文字だけでも240KB以内。
    parts = [body[i:i + 60000] for i in range(0, len(body), 60000)]
    for index, part in enumerate(parts, 1):
        suffix = f"（{index}/{len(parts)}）" if len(parts) > 1 else ""
        sns.publish(TopicArn=TOPIC, Subject=subject + suffix, Message=part)


def ban(image, keys, reason):
    """匿名IDとログインした利用者を利用停止の表へ載せる。既に載っていれば上書きしない。"""
    anon = image.get("anon", {}).get("S", "")
    user = image.get("user", {}).get("S", "")
    targets = ([f"ban#a#{anon}"] if anon and not anon.startswith("user-") else []) + ([f"ban#u#{user}"] if user else [])
    at = datetime.datetime.now(JST).isoformat(timespec="seconds")
    for pk in targets:
        try:
            ddb.put_item(
                TableName=STATE_TABLE, ConditionExpression="attribute_not_exists(pk)",
                Item={"pk": {"S": pk}, "source": {"S": "auto"}, "at": {"S": at}, "reason": {"S": reason},
                      "question": {"S": image.get("question", {}).get("S", "")[:500]},
                      "conv_pk": keys["pk"], "conv_sk": keys["sk"]},
            )
        except ddb.exceptions.ConditionalCheckFailedException:
            pass
    return targets


def _queue_and_maybe_send(entry=None):
    """entry を控えに足し、前回の送信から15分たっていれば控えをまとめて送る"""
    now = int(time.time())
    key = {"pk": {"S": "alert#flag"}}
    if entry:
        item = ddb.update_item(
            TableName=STATE_TABLE, Key=key, ReturnValues="ALL_NEW",
            UpdateExpression="SET pending = list_append(if_not_exists(pending, :empty), :e)",
            ExpressionAttributeValues={":e": {"L": [{"S": json.dumps(entry, ensure_ascii=False)}]}, ":empty": {"L": []}},
        )["Attributes"]
    else:
        item = ddb.get_item(TableName=STATE_TABLE, Key=key).get("Item")
        if not item:
            return
    pending = [json.loads(x["S"]) for x in item.get("pending", {}).get("L", [])]
    sent_at = int(item.get("sent_at", {}).get("N", "0"))
    if not pending or now - sent_at < QUIET:
        return
    try:
        # 送る権利を取る（同時に動いた別の起動と二重に送らない）。取れたら控えを空にする
        ddb.update_item(
            TableName=STATE_TABLE, Key=key,
            UpdateExpression="SET sent_at = :now, pending = :empty",
            ConditionExpression="(attribute_not_exists(sent_at) OR sent_at = :old) AND pending = :pending",
            ExpressionAttributeValues={":now": {"N": str(now)}, ":empty": {"L": []}, ":old": {"N": str(sent_at)}, ":pending": item["pending"]},
        )
    except ddb.exceptions.ConditionalCheckFailedException:
        return
    try:
        _notify(pending)
    except Exception:
        # 取得・送信に失敗した分を控えに戻し、次の起動で再試行する。
        ddb.update_item(
            TableName=STATE_TABLE, Key=key,
            UpdateExpression="SET pending = list_append(:failed, if_not_exists(pending, :empty)), sent_at = :old",
            ExpressionAttributeValues={":failed": item["pending"], ":empty": {"L": []}, ":old": {"N": str(sent_at)}},
        )
        raise


def handler(event, _context):
    if "Records" not in event:
        # 15分おきの起動：待っている知らせを送り出す
        _queue_and_maybe_send()
        return {"flushed": True}
    for record in event["Records"]:
        if record.get("eventName") != "INSERT":
            continue
        image = record["dynamodb"].get("NewImage", {})
        if "flag" in image:
            continue
        question = image.get("question", {}).get("S", "")
        if "prebaked" in image:
            # 質問も回答もこちらで用意した文なので、モデルに聞かずに normal とする
            flag, reason = "normal", "事前回答"
        else:
            flag, reason = classify(question, image.get("answer", {}).get("S", ""))
        keys = record["dynamodb"]["Keys"]
        try:
            ddb.update_item(
                TableName=TABLE, Key=keys,
                UpdateExpression="SET flag = :f, flag_reason = :r",
                ConditionExpression="attribute_not_exists(flag)",
                ExpressionAttributeValues={":f": {"S": flag}, ":r": {"S": reason}},
            )
        except ddb.exceptions.ConditionalCheckFailedException:
            continue  # 別の起動が先に書いた（再試行で同じ行が来たとき）
        print(json.dumps({"flag": flag, "reason": reason, "sk": keys.get("sk", {}).get("S")}, ensure_ascii=False))
        if flag == "abuse":
            print(json.dumps({"banned": ban(image, keys, reason)}))
        if flag in ALERT:
            _queue_and_maybe_send({"keys": keys, "flag": flag, "reason": reason})
    return {"ok": True}
