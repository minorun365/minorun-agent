"""検索の本文を保ったまま、繰り返す出典の表記をまとめる。"""

import collections
import json
import re


# 出典は build_kb.py が1行で出力する。途中で切れた出典は対象にしない。
_CITATION = re.compile(r"出典: [^\n]*?https?://[^\s]+")


def compact_sources(rows):
    """重複する出典だけを参照に置換する。年・本文・検索順位はそのまま。"""
    original = {"results": rows}
    counts = collections.Counter(
        match.group() for row in rows for match in _CITATION.finditer(row["text"])
    )
    # 本文中の表記と衝突する場合は、元の結果を返す。
    if any("[出典参照" in row["text"] for row in rows):
        return original
    repeated = {text: f"[出典参照{n}]" for n, text in enumerate(
        (text for text, count in counts.items() if count > 1), 1
    )}
    if not repeated:
        return original
    result = {
        "results": [
            {**row, "text": _CITATION.sub(lambda m: repeated.get(m.group(), m.group()), row["text"])}
            for row in rows
        ],
        "sources": {label: text for text, label in repeated.items()},
    }
    # 短い出典では対応表のほうが長くなることがある。
    if len(json.dumps(result, ensure_ascii=False)) >= len(json.dumps(original, ensure_ascii=False)):
        return original
    return result
