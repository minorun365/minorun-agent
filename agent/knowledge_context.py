"""検索の本文を保ったまま、繰り返す出典の表記をまとめる。"""

import collections
import json
import re


# 出典は build_kb.py が1行で出力する。途中で切れた出典は対象にしない。
_CITATION = re.compile(r"出典: [^\n]*?https?://[^\s]+")
# build_kb.py が書く行の先頭。ナレッジベースは行の途中でも断片を切る。
_LINE_START = ("- [", "#", "この文書は", "出典の種類", "「筆者は")
_FIRST_SOURCE = re.compile(r"出典: ([^\n]*?) https?://")


def mark_fragments(rows):
    """行の途中から始まる断片に、その行の出典を先頭で示す。

    主語が前の断片に残ると「キャンプが好き。」のような文だけが届き、
    回答者が一人称の「私」のことと読み違える。本文は変えない。
    """
    marked = []
    for row in rows:
        text = row["text"]
        source = _FIRST_SOURCE.search(text)
        head = text[:source.start()].strip() if source else ""
        if not source or not head or text.lstrip().startswith(_LINE_START) or "\n" in head:
            marked.append(row)
            continue
        label = source.group(1)
        marked.append({**row, "text": f"[前の行の途中から。この文は「{label}」の行の続きで、主語はその出典が紹介する人・物。みのるん本人のこととは限らない] {text}"})
    return marked


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
