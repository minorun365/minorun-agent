"""回答の不足を意味で判定し、1回の追加調査へつなぐ。"""
from pathlib import Path
import json
import logging

import topics

SEARCH_TOOLS = frozenset({
    "fetch_url",
    "search_sessions", "search_2026", "search_past", "search_web", "search_recent",
    "search_whats_new", "search_aws_blog", "get_announcements",
    "search_japanese", "search_x_users", "search_x_posts",
})

CHECK = (Path(__file__).with_name("check.local.txt").read_text().strip() if Path(__file__).with_name("check.local.txt").exists() else "")


def needed(question, answer, history, tools_used, audit=None):
    try:
        raw = topics._ask(CHECK, json.dumps({
            "question": question, "answer": answer,
            "history": history, "tools_used": tools_used,
        }, ensure_ascii=False))
        data = json.loads(raw)
        if type(data.get("needed")) is not bool:
            raise ValueError("invalid research decision")
        if audit is not None:
            audit.update(needed=data["needed"], reason=str(data.get("reason", ""))[:240], decision_status="ok")
        return data["needed"]
    except Exception as error:
        logging.warning("research_decision_failed: %s", type(error).__name__)
        # 判定障害でも既存の追加調査経路を保つ。
        fallback = "いまは情報がありません" in answer and not {"search_web", "search_recent"} & set(tools_used)
        if audit is not None:
            audit.update(needed=bool(fallback), reason="判定失敗時の既存条件", decision_status="fallback")
        return fallback
