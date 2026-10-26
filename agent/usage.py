"""要求ごとのモデル使用量。AWSが返したusageだけを加算し、未取得をゼロ扱いしない。"""
import contextvars
import hashlib
import threading
from pathlib import Path

from strands.models.bedrock import BedrockModel

CURRENT = contextvars.ContextVar("turn_usage", default=None)
# Oregon / Global / Standard、2026-10-26確認。100万トークンあたりUSD。
RATES = {
    "global.anthropic.claude-sonnet-5-5": (2, 10, .10, 2.50),
    "global.anthropic.claude-haiku-5-5": (.10, .50, .01, .125),
}
KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheWriteInputTokens")
REVISION = hashlib.sha256(b"".join(p.name.encode() + p.read_bytes()
    for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()[:16]


class Ledger:
    def __init__(self):
        self.calls = []
        self.condition = threading.Condition()
        self.pending = 0
        self.prebaked = False  # 事前回答はモデルを呼ばないので、呼び出しが無くても費用0で確定する

    def begin(self):
        with self.condition:
            self.pending += 1

    def end(self, model, phase, usage, error=False):
        valid = isinstance(usage, dict) and all(
            type(usage.get(k)) is int and usage[k] >= 0 for k in KEYS[:2])
        tokens = {k: usage.get(k, 0) for k in KEYS} if valid else None
        valid = valid and all(type(v) is int and v >= 0 for v in tokens.values())
        row = {"model": model, "phase": phase, "error": error, "usage_available": bool(valid)}
        if valid:
            row["tokens"] = tokens
            if model in RATES:
                row["usd"] = sum(tokens[k] * rate for k, rate in zip(KEYS, RATES[model])) / 1_000_000
        with self.condition:
            self.calls.append(row)
            self.pending -= 1
            self.condition.notify_all()

    def snapshot(self, timeout=90):
        with self.condition:
            self.condition.wait_for(lambda: self.pending == 0, timeout=timeout)
            calls = list(self.calls)
            complete = self.pending == 0 and (bool(calls) or self.prebaked) and all("usd" in c for c in calls)
            return {"schema": 1, "code_revision": REVISION, "pricing_date": "2026-10-26",
                    "scope": "runtime_model_calls", "complete": complete, "pending_calls": self.pending,
                    "known_usd": sum(c.get("usd", 0) for c in calls),
                    "usd": sum(c["usd"] for c in calls) if complete else None,
                    "calls": calls}


PHASE = contextvars.ContextVar("usage_phase", default="answer")


class MeteredModel(BedrockModel):
    async def stream(self, *args, **kwargs):
        ledger = CURRENT.get()
        if ledger:
            ledger.begin()
        usage, failed = None, False
        try:
            async for event in super().stream(*args, **kwargs):
                if "usage" in event.get("metadata", {}):
                    usage = event["metadata"]["usage"]
                yield event
        except BaseException:
            failed = True
            raise
        finally:
            if ledger:
                ledger.end(self.config["model_id"], PHASE.get(), usage, failed)
