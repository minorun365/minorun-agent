"""システムプロンプト。変わらない部分（キャッシュする）と、会話ごとに変わる部分（話題の棚）に分ける。"""
from pathlib import Path

BASE = (Path(__file__).with_name("base.local.txt").read_text().strip() if Path(__file__).with_name("base.local.txt").exists() else "")


def dynamic(shelf, current, memories=None):
    """話題の棚（前の話題の要約）と、いまの話題の見出し。ログインした人なら、前の会話から覚えていること。"""
    lines = []
    if memories:
        lines.append("# この人について前の会話から覚えていること\n答えに関係するときだけ自然に生かします（「覚えています」とわざわざ言わない）。")
        lines += [f"- {m}" for m in memories]
    if current:
        lines.append(f"# いまの話題\n{current}")
    if shelf:
        lines.append("# これまでの話題（要約）\n利用者が「さっきの話」と言ったら、ここを手がかりにします。")
        for item in shelf[-8:]:
            lines.append(f"- {item['heading']}：{item['summary']}")
    return "\n".join(lines) or "# いまの話題\n（会話の始まり）"
