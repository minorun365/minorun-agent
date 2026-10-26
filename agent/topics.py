"""話題の判定と要約（補助モデル Haiku 5.5）。どちらも利用者を待たせない位置で動かす。

判定：質問が届いたら回答と同時に始め、「同じ・新しい・戻る」と見出しを返す。迷ったら「同じ」。
要約：回答を返し終えた後、話題が変わっていたら前の話題を200字ほどにまとめて棚へしまう。
"""
from pathlib import Path

import json
import re
import secrets

import boto3
import usage

from settings import MODEL_AUX, REGION

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def _json_ok(text):
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def _ask(system, text, max_tokens=800):
    # 考える分にもトークンを使うので、上限は本文の長さより十分大きくする（少ないと本文が空で返る）
    max_tokens = max(max_tokens, 2000)
    ledger = usage.CURRENT.get()
    phase = "topic" if system == JUDGE else "suggest" if system == NEXT else "summary" if system == SUMMARY else "research_check"
    if ledger:
        ledger.begin()
    res, failed = {}, False
    try:
        res = _bedrock.converse(
            modelId=MODEL_AUX, system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": text}]}],
            inferenceConfig={"maxTokens": max_tokens},
            additionalModelRequestFields={"output_config": {"effort": "low"}},
        )
    except BaseException:
        failed = True
        raise
    finally:
        if ledger:
            ledger.end(MODEL_AUX, phase, res.get("usage"), failed)
    return "".join(b.get("text", "") for b in res["output"]["message"]["content"])


JUDGE = (Path(__file__).with_name("judge.local.txt").read_text().strip() if Path(__file__).with_name("judge.local.txt").exists() else "")


def judge(current, shelf, question):
    listing = "\n".join(f"{i}: {s['heading']}" for i, s in enumerate(shelf)) or "（なし）"
    text = f"いまの話題: {current or '（なし。会話の最初の質問）'}\n棚の話題:\n{listing}\n新しい質問: {question}"
    try:
        raw = _ask(JUDGE, text, 400)
        data = next(json.loads(m) for m in reversed(re.findall(r"\{[^{}]*\}", raw)) if _json_ok(m))
        if data.get("decision") not in ("same", "new", "back"):
            raise ValueError(raw)
        if not current:
            data["decision"] = "new"
        if data["decision"] == "same":
            data["heading"] = current
        if data["decision"] == "back":
            if isinstance(data.get("back_index"), int) and 0 <= data["back_index"] < len(shelf):
                data["heading"] = shelf[data["back_index"]]["heading"]
            else:
                data["decision"] = "new"
        return data
    except Exception as e:  # 判定に失敗したら同じ話題として扱う
        print(json.dumps({"topic_judge_error": type(e).__name__, "raw": (locals().get("raw") or "")[:200]}, ensure_ascii=False))
        if not current:
            return {"decision": "new", "heading": _heading(question), "back_index": None}
        return {"decision": "same", "heading": current, "back_index": None}


def _heading(question):
    q = re.sub(r"\s+", " ", question).strip()
    return q[:15]


SUMMARY_GROUNDING = (Path(__file__).with_name("summary_grounding.local.txt").read_text().strip() if Path(__file__).with_name("summary_grounding.local.txt").exists() else "")

SUMMARY = (Path(__file__).with_name("summary.local.txt").read_text().strip() if Path(__file__).with_name("summary.local.txt").exists() else "")


def summarize(heading, transcript):
    try:
        return _ask(SUMMARY, f"話題: {heading}\n\n{transcript}", 1200).strip()
    except Exception as e:
        print(json.dumps({"topic_summary_error": type(e).__name__}))
        return transcript[-300:]


NEXT = (Path(__file__).with_name("next.local.txt").read_text().strip() if Path(__file__).with_name("next.local.txt").exists() else "")


def suggest_next(current, shelf, question, answer=""):
    """次の質問の候補を3つ（深掘り2つ＋別の話題1つ）作る。answer があれば回答で触れなかった続きを聞かせる。失敗したら空。"""
    past = "、".join(s["heading"] for s in shelf[-4:]) or "（なし）"
    text = f"いまの話題: {current or '（なし）'}\nこれまでの話題: {past}\n直前の質問: {question}"
    angle = secrets.choice((
        "最近のAWSサービスの更新（生成AI以外も）",
        "Jam・GameDay・チョークトークから参加目的に合うセッションを探す",
        "会期の曜日と昼前・昼食後からセッションを探す",
        "ラスベガスでの食事や移動",
        "参加者の交流やコミュニティ",
        "みのるんの技術ナレッジやこのアプリの作り",
    ))
    text += f"\n今回の別の話題の切り口: {angle}。直前や棚と重なるなら他の話題を選ぶ。"
    if answer:
        text += f"\n直前の回答（この続きを深掘りにする）: {answer[:600]}"
    try:
        raw = _ask(NEXT, text, 300)
        # 言い直して配列を2つ続けて返すことがあるので、最後に読める配列（言い直した方）を使う
        items = next(json.loads(m) for m in reversed(re.findall(r"\[[^\[\]]*\]", raw)) if _json_ok(m))
        items = [re.sub(r"\s+", " ", str(i)).strip() for i in items if str(i).strip()]
        items = [i if i.endswith(("？", "?")) else i + "？" for i in items]
        return list(dict.fromkeys(i for i in items if len(i) <= 28 and i != question))[:3]
    except Exception as e:
        print(json.dumps({"suggest_error": type(e).__name__, "raw": (locals().get("raw") or "")[:200]}, ensure_ascii=False))
        return []
