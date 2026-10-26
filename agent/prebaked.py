"""用意した質問の事前回答。質問が完全に一致したら、モデルを呼ばずに作っておいた回答を流す。

正本は knowledge/prebaked/<topic>.json（Claude Code が知識のまとめを読んで書く）。
knowledge/tools/prebaked.py build が、知識の指紋が今と一致する話題だけをこのフォルダの prebaked.json へ写す。
深掘りの回答は親の質問に結び付けてあり、同じ会話で親を事前回答で答えた直後だけ使う。
"""

import asyncio
import datetime
import json
import random
from pathlib import Path

JST = datetime.timezone(datetime.timedelta(hours=9))
_PATH = Path(__file__).with_name("prebaked.json")
_DATA = json.loads(_PATH.read_text()) if _PATH.exists() else {"first": {}, "follow": {}}
FIRST: dict = _DATA["first"]
FOLLOW: dict = _DATA["follow"]

# いまの用意した質問の回答（10/25〜10/26 の実測）は、最初の文字まで中央値4.0秒・約90字/秒で流れていた。
FIRST_TEXT = (3.2, 4.8)
CHARS_PER_SEC = 90


def _fresh(item, today):
    # 出発前のイベントの日付など、過ぎると古くなる回答は until の日まで使う
    return not item.get("until") or today <= item["until"]


def lookup(question, parent=None, today=None):
    """1問目の事前回答か、直前の事前回答に続く深掘りの回答。無い・期限切れなら None。"""
    today = today or datetime.datetime.now(JST).date().isoformat()
    item = None
    if question in FIRST:
        item = {"kind": "first", "q": question, **FIRST[question]}
    elif parent and f"{parent}\n{question}" in FOLLOW:
        item = {"kind": "follow", "q": question, "topic": FIRST[parent]["topic"], **FOLLOW[f"{parent}\n{question}"]}
    return item if item and _fresh(item, today) else None


def suggestions(item, rng=random):
    """深掘り4つから2つと、別の話題の用意した質問1つ。"""
    picks = rng.sample(item["next"], min(2, len(item["next"])))
    others = [q for q, v in FIRST.items() if v["topic"] != item["topic"]]
    return picks + ([rng.choice(others)] if others else [])


def judged(item, current):
    """話題の判定。1問目は用意した見出しで始め、深掘りは同じ話題のまま。"""
    if item["kind"] == "follow" or (current and current == item.get("heading")):
        return {"decision": "same", "heading": current, "back_index": None}
    return {"decision": "new", "heading": item["heading"], "back_index": None}


def _chunks(text, rng):
    i = 0
    while i < len(text):
        n = rng.randint(3, 8)
        yield text[i:i + n]
        i += n


async def play(item, rng=random, sleep=asyncio.sleep):
    """本番の回答と同じ速さで流す。待ち時間は少しぶれさせ、毎回同じ秒数にしない。"""
    first_text = rng.uniform(*FIRST_TEXT)
    gaps = [rng.uniform(0.3, 0.8)] + [rng.uniform(1.0, 1.8) for _ in item["tools"][1:]]
    for name, gap in zip(item["tools"], gaps):
        await sleep(gap)
        first_text -= gap
        yield {"type": "tool", "name": name}
    await sleep(max(first_text, 0.5))
    for piece in _chunks(item["answer"], rng):
        yield {"type": "text", "text": piece}
        await sleep(len(piece) / CHARS_PER_SEC * rng.uniform(0.7, 1.3))
