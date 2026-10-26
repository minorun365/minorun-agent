"""本人が撤回した説明を、再抽出後も検索用の知識から除く。"""
import re


def curate(source):
    if source.get("source_type") != "own":
        return source
    facts = []
    for fact in source.get("facts", []):
        text = fact["text"]
        if re.search(r"(?:PLAN|SPEC|TODO|KNOWLEDGE)\.md", text):
            continue
        if "Claude Code" in text and re.search(r"VS\s*Code", text) and fact["topic"] == "tech-ai-work":
            continue
        if "みのるん式にはKiro Crewが最も合う" in text:
            continue
        facts.append(fact)
    return {**source, "facts": facts}
