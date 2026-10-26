"""会話の保存。

- AgentCore Memory：会話中の文脈（やり取りと話題の棚）。1回答えるごとに丸ごとの控えを blob で積み、
  Runtime が入れ替わったら最新の控えから戻す。保持は7日（Memory 側の設定）
- AgentCore Memory の長期記憶：Google でログインした人だけ、やり取りを会話の形でも積む。Memory が好みと事実を
  抜き出して /users/{actorId}/ の下に残し、次の会話の始めに思い出す（blob は抜き出しの対象にならない）
- DynamoDB：後から分析するための1問1行。期限なし。書く前にメールアドレスと電話番号の形を伏せる
"""

import base64
import datetime
import gzip
import json
import re
import time

import boto3

from settings import JST, LOG_TABLE, MEMORY_ID, REGION

_memory = boto3.client("bedrock-agentcore", region_name=REGION)
_table = boto3.resource("dynamodb", region_name=REGION).Table(LOG_TABLE) if LOG_TABLE else None

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 日本の番号（0 か +81 で始まる）と、北米の 3-3-4 の形。日付（2026-12-02）や時刻は当たらない
PHONE_JP = re.compile(r"(?<![\d-])(?:\+81[-\s]?\d{1,4}|0\d{1,4})[-\s]?\d{1,4}[-\s]?\d{3,4}(?![\d-])")
PHONE_US = re.compile(r"(?<![\d-])(?:\+1[-\s]?)?\(?\d{3}\)?[-\s.]\d{3}[-\s.]\d{4}(?![\d-])")


def redact(text):
    text = EMAIL.sub("[メールアドレス]", text or "")
    text = PHONE_JP.sub("[電話番号]", text)
    return PHONE_US.sub("[電話番号]", text)


def save_snapshot(actor, session, messages, state):
    if not MEMORY_ID:
        return
    blob = base64.b64encode(gzip.compress(json.dumps({"v": 1, "messages": messages, "state": state},
                                                     ensure_ascii=False, default=str).encode())).decode()
    _memory.create_event(memoryId=MEMORY_ID, actorId=actor, sessionId=session,
                         eventTimestamp=datetime.datetime.now(datetime.timezone.utc),
                         payload=[{"blob": json.dumps({"kind": "snapshot", "gz": blob})}])


def load_snapshot(actor, session):
    if not MEMORY_ID:
        return None
    latest, snapshot, token = None, None, None
    while True:
        kw = {"nextToken": token} if token else {}
        res = _memory.list_events(memoryId=MEMORY_ID, actorId=actor, sessionId=session,
                                  includePayloads=True, maxResults=100, **kw)
        for ev in res.get("events", []):
            # ログイン利用者は同じセッションへ長期記憶用の会話も記録する。
            # その方が新しくても、復元には最新の snapshot を選ぶ。
            for item in ev.get("payload", []):
                raw = item.get("blob")
                if raw is None:
                    continue
                data = json.loads(raw) if isinstance(raw, str) else raw
                if data.get("kind") == "snapshot" and (latest is None or ev["eventTimestamp"] > latest):
                    latest, snapshot = ev["eventTimestamp"], data
        token = res.get("nextToken")
        if not token:
            break
    if snapshot is None:
        return None
    return json.loads(gzip.decompress(base64.b64decode(snapshot["gz"])))


def remember_turn(actor, session, question, answer):
    if not MEMORY_ID:
        return
    _memory.create_event(memoryId=MEMORY_ID, actorId=actor, sessionId=session,
                         eventTimestamp=datetime.datetime.now(datetime.timezone.utc),
                         payload=[{"conversational": {"content": {"text": redact(question)}, "role": "USER"}},
                                  {"conversational": {"content": {"text": redact(answer)[:4000]}, "role": "ASSISTANT"}}])


def recall(actor, query, limit=6):
    """ログインした人について覚えていること（好み・事実）を、質問に近い順に返す。"""
    if not MEMORY_ID:
        return []
    found = []
    for kind in ("preferences", "facts"):
        res = _memory.retrieve_memory_records(memoryId=MEMORY_ID, namespace=f"/users/{actor}/{kind}",
                                              searchCriteria={"searchQuery": query[:500], "topK": 4}, maxResults=4)
        found += [r["content"]["text"] for r in res.get("memoryRecordSummaries", []) if r.get("content", {}).get("text")]
    return found[:limit]


def log_turn(row):
    if _table is None:
        return
    now = datetime.datetime.now(JST)
    item = {**row, "question": redact(row.get("question")), "answer": redact(row.get("answer")),
            "created_at": now.isoformat(timespec="seconds"), "sk": f"{now.isoformat(timespec='milliseconds')}"}
    if "initial_answer" in item:
        item["initial_answer"] = redact(item["initial_answer"])
    if "research" in item:
        item["research"] = {**item["research"], "reason": redact(item["research"].get("reason"))}
    _table.put_item(Item=json.loads(json.dumps(item, ensure_ascii=False, default=str), parse_int=int,
                                    parse_float=lambda x: __import__("decimal").Decimal(x)))


def elapsed_ms(start):
    return int((time.time() - start) * 1000)
