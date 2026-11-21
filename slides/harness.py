"""スライド作成機能から AgentCore Harness（マネージドのエージェント）を呼ぶ（2026-11-21）。

Harness の入れ物はスライド作成と同じコンテナで、エージェントがシェルとファイルの道具で書く→焼く→測る→画像を見て直す、を自分で回す。
モデルは呼び出しごとに選ぶ。ふだんは Claude API（API キーは AgentCore Identity）、クレジットが尽きる手前からは Bedrock。
Claude API で失敗したら、同じセッションのまま Bedrock を指定して続きを書かせる（作業フォルダも会話もそのまま残る）。
"""

import json
import os
import re
import secrets
import time

import boto3
from botocore.config import Config

import credit

REGION = os.environ.get("AWS_REGION", "us-west-2")
HARNESS_ARN = os.environ.get("HARNESS_ARN", "")
BEDROCK_MODEL = os.environ.get("SLIDES_MODEL", "global.anthropic.claude-opus-5-5")
API_MODEL = os.environ.get("SLIDES_API_MODEL", "claude-opus-5-5")
MAX_TOKENS = 32000

client = boto3.client("bedrock-agentcore", region_name=REGION,
                      config=Config(read_timeout=1800, connect_timeout=10, retries={"max_attempts": 1}))


def model_config(route):
    if route == "api":
        return {"liteLlmModelConfig": {"modelId": f"anthropic/{API_MODEL}", "apiKeyArn": credit.KEY_ARN, "maxTokens": MAX_TOKENS}}
    return {"bedrockModelConfig": {"modelId": BEDROCK_MODEL, "maxTokens": MAX_TOKENS}}


PERMANENT = re.compile(r"credit balance|billing|insufficient|quota|payment|401|402|403|authenticat|permission|invalid.{0,10}api.?key|revoked",
                       re.I)


def phase(name, raw, seen):
    """道具の呼び出し1つを、画面に出す進み具合の段階と一言にする。返すのは (段階, 一言) か None。
    段階はスライド作成機能の status の stage（writing・rendering・checking）。一言は利用者に見せるので、道具の中身（記事の文やコマンド）は出さない"""
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        args = {}
    cmd = str(args.get("command") or args.get("cmd") or "")
    path = str(args.get("path") or args.get("file_path") or "")
    if name == "shell" and "check_deck" in cmd:
        seen["check"] = seen.get("check", 0) + 1
        return "rendering", f"書き出して余白を測っています（{seen['check']}回目）"
    if name == "shell" and "arch.py" in cmd:
        return "writing", "アーキテクチャ図を描いています"
    if (name == "shell" and ("illust.py" in cmd or "catalog.tsv" in cmd)) or "/tmp/ill/" in path:
        return "writing", "挿絵を選んでいます"
    if name == "read" and path.endswith(".png") and "/check/" in path:
        return "checking", "書き出したスライドを見直しています"
    if name in ("write", "edit") and path.endswith("deck.md"):
        return ("writing", "スライドを書いています") if not seen.get("check") else ("checking", "見直したところを直しています")
    if name == "shell" and "finish.py" in cmd:
        return "checking", "仕上げています"
    return None


def _invoke(session, text, system, route, progress=None):
    """1回呼んで、ストリームを最後まで読む。返すのは (使用量の合計, 最後の文, 止まった理由, エラー, 道具の数)。
    progress があれば、道具を使うたびに (段階, 一言) を渡す"""
    usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}
    out, stop, error, tools = [], None, None, 0
    blocks, seen = {}, {}
    try:
        r = client.invoke_harness(harnessArn=HARNESS_ARN, runtimeSessionId=session, model=model_config(route),
                                  systemPrompt=[{"text": system}], messages=[{"role": "user", "content": [{"text": text}]}])
        for ev in r["stream"]:
            kind, body = next(iter(ev.items()))
            if kind == "metadata":
                for k in usage:
                    usage[k] += body.get("usage", {}).get(k, 0) or 0
            elif kind == "contentBlockDelta" and "text" in body.get("delta", {}):
                out.append(body["delta"]["text"])
            elif kind == "contentBlockStart" and "toolUse" in body.get("start", {}):
                tools += 1
                blocks[body["contentBlockIndex"]] = [body["start"]["toolUse"].get("name", ""), ""]
            elif kind == "contentBlockDelta" and "toolUse" in body.get("delta", {}) and body["contentBlockIndex"] in blocks:
                blocks[body["contentBlockIndex"]][1] += body["delta"]["toolUse"].get("input", "")
            elif kind == "contentBlockStop" and body.get("contentBlockIndex") in blocks:
                name, raw = blocks.pop(body["contentBlockIndex"])
                step = phase(name, raw, seen)
                if step and progress:
                    try:
                        progress(*step)
                    except Exception:
                        pass  # 進み具合を書けなくても、作るのは続ける
            elif kind == "messageStop":
                stop = body.get("stopReason")
                if stop == "tool_use":
                    out = []  # 道具を使う前の独り言は捨て、最後の答えだけ残す
            elif kind in ("runtimeClientError", "internalServerException", "validationException"):
                error = f"{kind}: {body.get('message', '')}"
    except Exception as e:  # 呼び出しそのものの失敗（スロットリング・権限・ストリームの途中のエラー）
        error = f"{type(e).__name__}: {str(e)[:500]}"
    return usage, "".join(out).strip(), stop, error, tools


def run(job, text, system, route, progress=None):
    """1本を書かせる。返すのは {"usage": [...], "route": 最後に使った道, "error": ...}。
    usage は呼び出しごとに {input, output, model, usd} を並べる（監視ページが model で Claude API と Bedrock を分ける）"""
    session = f"slides-harness-{job}-{secrets.token_hex(8)}"
    calls, error = [], None
    for attempt in range(2):
        started = time.time()
        usage, answer, stop, error, tools = _invoke(session, text, system, route, progress)
        tokens = usage["inputTokens"] + usage["cacheReadInputTokens"] + usage["cacheWriteInputTokens"]
        usd = credit.usd_of(usage) if route == "api" else 0.0
        calls.append({"input": tokens, "output": usage["outputTokens"], "model": API_MODEL if route == "api" else BEDROCK_MODEL,
                      "usd": round(usd, 6), "route": route, "tools": tools, "stop": stop,
                      "seconds": round(time.time() - started, 1)})
        if route == "api":
            try:
                credit.add_slides(usd)
            except Exception as e:
                print(json.dumps({"job": job, "credit_ledger": "failed", "reason": type(e).__name__}))
        print(json.dumps({"job": job, "harness": route, "stop": stop, "tools": tools, "error": (error or "")[:300],
                          "answer": answer[-200:]}, ensure_ascii=False))
        if not error or route != "api":
            break
        # Claude API で止まった。待っても直らない失敗なら、その期は Bedrock に切り替える
        if PERMANENT.search(error):
            credit.switch_off(error)
        route = "bedrock"
        text = ("前の呼び出しはモデル側の都合で途中で止まりました。作業フォルダの今の状態を確かめて、"
                "続きから仕上げてください。最後に finish を実行するところまでが作業です。")
    return {"usage": calls, "route": route, "error": error, "session": session}
