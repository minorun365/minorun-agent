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
POINTS = json.loads(Path(__file__).with_name("moderation.local.json").read_text()).get("points", {}) if Path(__file__).with_name("moderation.local.json").exists() else {}
LIMIT = json.loads(Path(__file__).with_name("moderation.local.json").read_text()).get("limit", 0) if Path(__file__).with_name("moderation.local.json").exists() else 0
RULE_VERSION = json.loads(Path(__file__).with_name("moderation.local.json").read_text()).get("version", "") if Path(__file__).with_name("moderation.local.json").exists() else ""
JST = datetime.timezone(datetime.timedelta(hours=9))

bedrock = boto3.client("bedrock-runtime")
ddb = boto3.client("dynamodb")
sns = boto3.client("sns")

SYSTEM = (Path(__file__).with_name("system.local.txt").read_text().strip() if Path(__file__).with_name("system.local.txt").exists() else "")


def classify(question, answer):
    if not SYSTEM:
        raise RuntimeError("system.local.txt を設定してください")
    decision = classify_turn(question, answer, [])
    return decision["flag"], decision["reason"]


def classify_turn(question, answer, history):
    if not SYSTEM or not POINTS or LIMIT <= 0 or not RULE_VERSION:
        raise RuntimeError("system.local.txt と moderation.local.json を設定してください")
    text = json.dumps({"history": history, "question": question[:2000], "answer_head": (answer or "")[:600]}, ensure_ascii=False)
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
            violation = data.get("violation")
            evidence = data.get("evidence")
            if violation not in POINTS or not isinstance(evidence, list):
                continue
            valid_ids = {h["id"] for h in history}
            if any(not isinstance(e, str) or e not in valid_ids for e in evidence):
                continue
            return {"flag": data["flag"], "reason": str(data.get("reason") or "")[:30],
                    "violation": violation, "evidence": evidence}
    raise ValueError("分類応答が不正です")


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
             "abuse": "良識を逸脱した入力"}.get(flag, flag)
    query = {"v": "list", "d": at.date().isoformat()}
    if actor:
        query["u"] = identifier
    url = os.environ.get("ADMIN_BASE_URL", "https://agent.minoruonda.com/admin/") + "?" + urlencode(query)
    tools = "、".join(t.get("S", "") for t in image.get("tools", {}).get("L", [])) or "なし"
    return (
        f"日時：{at:%Y/%m/%d}（{weekday}）{at:%H:%M:%S} JST\n"
        f"判定：{label}（{flag}）\n判定理由：{value('flag_reason', entry.get('reason', '記録なし'))}\n"
        f"加点：{image.get('moderation_delta', {}).get('N', '未記録')}・累計：{image.get('moderation_total', {}).get('N', '未記録')}\n"
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
    subject = "みのるんエージェント｜利用停止" if any(e.get("banned") for e in entries) else "みのるんエージェント｜要注意の入力"
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


def _value(row, name):
    return row.get(name, {}).get("S", "")


def _actor(row):
    user, anon = _value(row, "user"), _value(row, "anon")
    return "u#" + user if user else "a#" + anon if anon else ""


def _read(table, key):
    return ddb.get_item(TableName=table, Key=key, ConsistentRead=True).get("Item", {})


def _history_rows(keys):
    rows, extra = [], {}
    while True:
        page = ddb.query(TableName=TABLE, ConsistentRead=True,
                         KeyConditionExpression="pk = :p AND sk <= :s",
                         ExpressionAttributeValues={":p": keys["pk"], ":s": keys["sk"]}, **extra)
        rows.extend(page.get("Items", []))
        if not page.get("LastEvaluatedKey"):
            return rows
        extra = {"ExclusiveStartKey": page["LastEvaluatedKey"]}


def _deliver(row):
    if not row.get("moderation_notify", {}).get("BOOL"):
        return
    keys = {k: row[k] for k in ("pk", "sk")}
    _queue_and_maybe_send({"keys": keys, "flag": _value(row, "flag"),
                          "reason": _value(row, "flag_reason"),
                          "banned": row.get("moderation_banned", {}).get("BOOL", False)})
    ddb.update_item(TableName=TABLE, Key=keys,
                    UpdateExpression="SET moderation_notify = :f",
                    ExpressionAttributeValues={":f": {"BOOL": False}})


def _score(row, history, decision, state, reset):
    """判定・加点・停止を一括確定する。競合時は呼び出し側で履歴から読み直す。"""
    actor = _actor(row)
    keys = {k: row[k] for k in ("pk", "sk")}
    epoch = _value(reset, "epoch")
    cutoff = _value(reset, "cutoff")
    old_score = int(state.get("score", {}).get("N", "0")) if _value(state, "epoch") == epoch else 0
    delta = POINTS[decision["violation"]] if actor and (not cutoff or _value(row, "sk") > cutoff) else 0
    total = old_score + delta
    banned = bool(actor and delta and total >= LIMIT)
    revision = int(state.get("revision", {}).get("N", "0"))
    at = datetime.datetime.now(JST).isoformat(timespec="milliseconds")
    values = {":f": {"S": decision["flag"]}, ":r": {"S": decision["reason"]},
              ":d": {"N": str(delta)}, ":t": {"N": str(total)}, ":v": {"S": RULE_VERSION},
              ":e": {"L": [{"S": e} for e in decision["evidence"]]},
              ":kind": {"S": decision["violation"]}, ":b": {"BOOL": banned},
              ":notify": {"BOOL": banned or decision["flag"] in ALERT}}
    tx = [{"Update": {"TableName": TABLE, "Key": keys,
           "ConditionExpression": "attribute_exists(pk) AND attribute_not_exists(flag)",
           "UpdateExpression": "SET flag=:f, flag_reason=:r, moderation_delta=:d, moderation_total=:t, moderation_version=:v, moderation_evidence=:e, moderation_kind=:kind, moderation_banned=:b, moderation_notify=:notify",
           "ExpressionAttributeValues": values}}]
    if actor:
        scorekey = {"pk": {"S": "moderation#" + actor + "#" + _value(row, "pk")}}
        update = {"TableName": STATE_TABLE, "Key": scorekey,
                  "UpdateExpression": "SET score=:s, revision=:next, epoch=:e",
                  "ExpressionAttributeValues": {":s": {"N": str(total)}, ":next": {"N": str(revision+1)}, ":e": {"S": epoch}},
                  "ConditionExpression": "revision=:old" if state else "attribute_not_exists(pk)"}
        if state:
            update["ExpressionAttributeValues"][":old"] = {"N": str(revision)}
        tx.append({"Update": update})
        condition = {"TableName": STATE_TABLE, "Key": {"pk": {"S": "moderation-reset#"+actor}},
                     "ConditionExpression": "epoch=:e" if reset else "attribute_not_exists(pk)"}
        if reset:
            condition["ExpressionAttributeValues"] = {":e": {"S": epoch}}
        tx.append({"ConditionCheck": condition})
    if banned:
        anon, user = _value(row, "anon"), _value(row, "user")
        targets = (["ban#a#"+anon] if anon and not anon.startswith("user-") else []) + (["ban#u#"+user] if user else [])
        for target in targets:
            tx.append({"Update": {"TableName": STATE_TABLE, "Key": {"pk": {"S": target}},
                       "UpdateExpression": "SET #src=if_not_exists(#src,:src), #at=if_not_exists(#at,:at), reason=if_not_exists(reason,:r), question=if_not_exists(question,:q), conv_pk=if_not_exists(conv_pk,:p), conv_sk=if_not_exists(conv_sk,:s), score=if_not_exists(score,:t)",
                       "ExpressionAttributeNames": {"#src": "source", "#at": "at"},
                       "ExpressionAttributeValues": {":src": {"S": "cumulative"}, ":at": {"S": at},
                         ":r": {"S": decision["reason"]}, ":q": {"S": _value(row, "question")[:500]},
                         ":p": keys["pk"], ":s": keys["sk"], ":t": {"N": str(total)}}}})
    ddb.transact_write_items(TransactItems=tx)
    print(json.dumps({"moderation": RULE_VERSION, "delta": delta, "total": total, "banned": banned}))


def process_conversation(keys):
    """同じ利用者・会話の保存済み未判定行を古い順に処理する。"""
    target = _read(TABLE, keys)
    if not target:
        raise RuntimeError("判定対象の会話行が取得できません")
    actor = _actor(target)
    for attempt in range(8):
        try:
            rows = [r for r in _history_rows(keys) if _actor(r) == actor]
            for index, row in enumerate(rows):
                if "flag" in row:
                    _deliver(row)
                    continue
                statekey = {"pk": {"S": "moderation#"+actor+"#"+_value(row,"pk")}}
                state = _read(STATE_TABLE, statekey) if actor else {}
                reset = _read(STATE_TABLE, {"pk": {"S": "moderation-reset#"+actor}}) if actor else {}
                history = [{"id": _value(h,"sk"), "question": _value(h,"question")[:2000],
                            "answer": _value(h,"answer")[:600]} for h in rows[max(0,index-12):index]]
                decision = ({"flag":"normal", "reason":"事前回答", "violation":"none", "evidence":[]}
                            if "prebaked" in row else classify_turn(_value(row,"question"), _value(row,"answer"), history))
                _score(row, history, decision, state, reset)
                _deliver(_read(TABLE, {k:row[k] for k in ("pk","sk")}))
            return
        except ddb.exceptions.TransactionCanceledException as exc:
            reasons = exc.response.get("CancellationReasons", [])
            if not reasons or any(r.get("Code") not in ("None", "ConditionalCheckFailed", "TransactionConflict") for r in reasons):
                raise
            time.sleep(min(.05 * (attempt+1), .4))
    raise RuntimeError("会話の加点が競合したため再試行します")


def handler(event, _context):
    if "Records" not in event:
        # 15分おきの起動：待っている知らせを送り出す
        _queue_and_maybe_send()
        return {"flushed": True}
    for record in event["Records"]:
        if record.get("eventName") == "INSERT":
            process_conversation(record["dynamodb"]["Keys"])
    return {"ok": True}
