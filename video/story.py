"""資料を読んで、3分の解説動画の台本（コマ割り・字幕・読み・図の出し方）を作る。

2段で読む：
  1. 読む：ページ画像を10枚ずつモデルに見せ、ページごとの要旨と、図があればその位置と読み取れることを書かせる。
     文字の無い画像だけの資料（Marp から書き出した PPTX など）でも、ここで中身を拾える。
  2. 組む：1の結果（文字だけ）から、見る人の問いを決めて筋を組み、コマごとの字幕と見せ方を書かせる。

組み方は、みのるんが仕事で作らせた解説動画で効いた決まりを写した（kb-html-briefing の
「理解を助けるアニメ・解説は、学ぶ人の問いから組む」）。手元の素材を順に並べず、見終わったときに答えられる問いから組む。
"""

import base64
import json
import os
import re
import time
from pathlib import Path

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL = os.environ.get("VIDEO_MODEL", "global.anthropic.claude-opus-5-5")
API_MODEL = os.environ.get("VIDEO_API_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("VIDEO_EFFORT", "medium")
BATCH = 10

_bedrock = None
API = {"client": None, "off": None, "busy_until": 0.0}
USAGE = {"input": 0, "output": 0, "calls": 0}


def _wif():
    return {"federation_rule_id": os.environ.get("CLAUDE_WIF_RULE_ID", ""),
            "organization_id": os.environ.get("CLAUDE_WIF_ORGANIZATION_ID", ""),
            "service_account_id": os.environ.get("CLAUDE_WIF_SERVICE_ACCOUNT_ID", "") or None}


def _api_on():
    w = _wif()
    return (os.environ.get("ANTHROPIC_PRIMARY", "on") == "on" and w["federation_rule_id"] and w["organization_id"]
            and not API["off"] and time.time() >= API["busy_until"])


def _identity_token():
    sts = boto3.client("sts", region_name="us-east-1")
    return sts.get_web_identity_token(Audience=["https://api.anthropic.com"], SigningAlgorithm="RS256",
                                      DurationSeconds=900)["WebIdentityToken"]


def _permanent(error):
    """待っても直らない失敗か（slides/handler.py と同じ線引き）"""
    import anthropic
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    if isinstance(error, anthropic.WorkloadIdentityError):
        return not re.search(r"HTTP 5\d\d", str(error))
    if type(error).__name__ == "ClientError" and "GetWebIdentityToken" in str(error):
        return True
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code == 402 or getattr(error, "type", None) == "billing_error":
            return True
        if error.status_code == 400 and "credit balance" in str(error).lower():
            return True
    return False


def _ask_api(parts, rules, max_tokens):
    import anthropic
    import httpx
    if API["client"] is None:
        API["client"] = anthropic.Anthropic(
            credentials=anthropic.WorkloadIdentityCredentials(identity_token_provider=_identity_token, **_wif()),
            max_retries=0, timeout=httpx.Timeout(connect=5.0, read=300.0, write=60.0, pool=5.0))
    content = []
    for p in parts:
        if "image" in p:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(p["image"]).decode()}})
        else:
            content.append({"type": "text", "text": p["text"]})
    with API["client"].messages.stream(model=API_MODEL, system=rules, max_tokens=max_tokens,
                                       messages=[{"role": "user", "content": content}],
                                       output_config={"effort": EFFORT}) as stream:
        final = stream.get_final_message()
    u = final.usage
    return "".join(b.text for b in final.content if b.type == "text"), \
        (u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0), u.output_tokens)


def _ask_bedrock(parts, rules, max_tokens):
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION,
                                config=Config(read_timeout=600, connect_timeout=10, retries={"max_attempts": 2}))
    content = [{"image": {"format": "jpeg", "source": {"bytes": p["image"]}}} if "image" in p else {"text": p["text"]}
               for p in parts]
    res = _bedrock.converse(modelId=MODEL, system=[{"text": rules}], messages=[{"role": "user", "content": content}],
                            inferenceConfig={"maxTokens": max_tokens},
                            additionalModelRequestFields={"output_config": {"effort": EFFORT}})
    u = res.get("usage", {})
    return "".join(c.get("text", "") for c in res["output"]["message"]["content"]), \
        (u.get("inputTokens", 0), u.get("outputTokens", 0))


def ask(parts, rules, max_tokens=16000):
    """Claude API を先に呼び、使えないときだけ Bedrock（AGENTS.md の決まり）。parts は {"text"} か {"image": bytes} の並び"""
    text = None
    if _api_on():
        try:
            text, (i, o) = _ask_api(parts, rules, max_tokens)
        except Exception as e:
            if _permanent(e):
                API["off"] = type(e).__name__
            else:
                API["busy_until"] = time.time() + 60
            print(json.dumps({"model_route": "bedrock", "reason": type(e).__name__, "detail": str(e)[:300]}))
    if text is None:
        text, (i, o) = _ask_bedrock(parts, rules, max_tokens)
    USAGE["input"] += i
    USAGE["output"] += o
    USAGE["calls"] += 1
    return text


def _json(text):
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    body = m.group(1) if m else text[text.find("{"):text.rfind("}") + 1]
    return json.loads(body)


READ_RULES = """あなたは資料を読み解く係です。渡されたページ画像（とページから抜いた文字・発表者メモ）を1ページずつ読み、あとで解説動画の台本を書く人のためのメモを JSON で返します。

ページごとに次を書きます。
- n：ページ番号
- role：表紙・自己紹介・問いかけ・説明・図解・事例・数字・まとめ・その他 のどれか
- gist：このページが言っていることを1〜2文で。画像の中の文字も読み、文字が無くても図や写真から読み取る
- figure：図・グラフ・表・画面写真・イラストで中身を語っているものがあれば1つだけ選び、次の形で。飾りの絵や背景・ロゴだけなら null
  - box：その図がページのどこにあるか。ページ全体を左上(0,0)・右下(1,1)とした [左, 上, 右, 下] の割合。図の周りを少しだけ余らせる
  - what：何の図か（例「会議中の人と3体のAIが同時に動く関係図」「売上の月別の棒グラフ」）
  - reading：図から読み取れること。矢印の向き・数字の大小・左右の対比など、図を見ないと分からないことを具体的に
- keep：解説に欠かせないページなら true（表紙・自己紹介・同じことの繰り返しは false）

返すのは ```json で囲んだ {"pages": [...]} だけです。
"""


STORY_RULES = """あなたは、資料の中身をずんだもんの声で読み上げる3分の解説動画の台本作家です。
資料のメモ（ページごとの要旨と図の読み取り）から、初めてこの資料に触れる人が「なるほど」と分かる動画の台本を JSON で書きます。

# 組み方（いちばん大事）
1. 資料を頭から順に要約しない。まず、見終わった人が自分の言葉で答えられるべき問いを3つに絞る。問いは資料のいちばん大事な主張から逆算する。
2. 冒頭のコマで、見る人が抱えていそうな疑問か困りごとを1つ投げて引き込む（資料の問いかけページがあれば使う）。
3. 本編は問いごとに組む。それぞれ「これが無いと何が困るか」を先に見せ、そのあとで資料の答え（仕組み・やり方）がそれをどう解決するかを見せる。意図は、防いでいる失敗と並べたときにだけ伝わる。
4. 1つのコマで新しい考えは1つだけ。前のコマで出ていない用語をいきなり使わない。用語は初出で一言の言い換えを添える。
5. 資料の図は、図がある所でこそ映す。図を映したコマでは、図のどこを見ればよいかを字幕で言い、box でその場所を光らせる。「この図のように」で済ませず、図から読めることを言葉にする。
6. 最後のコマで3つの問いへの答えを1つずつ短く言い切って締める。
7. 資料に無い事実や数字を足さない。資料の主張を誇張しない。

# 尺
読み上げ（say）の合計を全角で1050〜1150字にする（ずんだもんの声を1.25倍で読んで約3分）。コマは8〜12個。1行（字幕1枚）は全角16〜40字で、1文か読点で切れる所まで。

# 口調
です・ます調のやさしい説明文。語尾を「〜のだ」にしない。視聴者に呼びかけすぎない。体言止めや「〜なんです！」の連発をしない。

# コマの種類（kind）と見せ方
- "title"：題と1行の副題。lines は1〜2行
- "questions"：この動画で答える3つの問いを順に出す。lines の reveal で1つずつ足す
- "page"：資料のページを映す。page にページ番号。図を大きく見せたいときは focus に図の box を入れる（ページの一部へ寄る）。各行の box で、その行が話している場所を光らせる（focus を入れたときも box はページ全体に対する割合で書く）
- "diagram"：資料の言葉で作る簡単な図。type は "steps"（順番に進む流れ。3〜5個）・"compare"（左右の対比。items はちょうど2個で、左が困る側・右が解決した側）・"points"（並ぶ要点。2〜4個）。各行の reveal で何個目まで見せるかを決め、話に合わせて1つずつ足す
- "recap"：3つの問いと答え。lines の reveal で1つずつ足す

page と diagram を交互に使い、page を半分以上にする。同じページを2回使わない。

# 読み（say）
字幕（text）とは別に、ずんだもんに読ませる文（say）を書く。英字の語・略語・記号はカタカナにする（例 Claude→クロード、AI→エーアイ、AWS→エーダブリューエス、MCP→エムシーピー、3体→さんたい）。数字は読み間違えやすいものだけ仮名に。それ以外は text と同じでよい。

# 返す形
```json で囲んだ次の形だけを返す。
{
  "title": "動画の題（全角20字まで）",
  "subtitle": "1行の副題（全角25字まで）",
  "questions": ["問い1", "問い2", "問い3"],
  "answers": ["答え1（全角30字まで）", "答え2", "答え3"],
  "scenes": [
    {"kind": "title", "heading": "", "lines": [{"text": "", "say": ""}]},
    {"kind": "page", "heading": "コマの見出し（全角20字まで）", "page": 7, "focus": [0.05, 0.2, 0.95, 0.9],
     "lines": [{"text": "", "say": "", "box": [0.1, 0.3, 0.4, 0.8]}]},
    {"kind": "diagram", "heading": "", "type": "compare",
     "items": [{"label": "全角12字まで", "note": "全角24字まで"}, {"label": "", "note": ""}],
     "lines": [{"text": "", "say": "", "reveal": 1}]}
  ]
}
"""


def read_pages(pages, title_hint=""):
    """1段目：ページ画像を10枚ずつ読ませる。戻り値は ページ番号 → メモ"""
    notes = {}
    for start in range(0, len(pages), BATCH):
        chunk = pages[start:start + BATCH]
        parts = [{"text": f"資料{('「' + title_hint + '」') if title_hint else ''}の {chunk[0]['n']}〜{chunk[-1]['n']} ページです（全{len(pages)}ページ）。"}]
        for p in chunk:
            extra = (f"\n抜いた文字：{p['text'][:1200]}" if p["text"] else "\n抜いた文字：なし（画像から読んでください）") + \
                    (f"\n発表者メモ：{p['notes'][:800]}" if p.get("notes") else "")
            parts.append({"text": f"--- {p['n']} ページ ---{extra}"})
            parts.append({"image": Path(p["look"]).read_bytes()})
        for attempt in range(2):
            try:
                got = _json(ask(parts, READ_RULES, max_tokens=8000))
                break
            except (ValueError, KeyError) as e:
                if attempt:
                    raise
                print(json.dumps({"read_retry": type(e).__name__}))
        for row in got.get("pages", []):
            if isinstance(row, dict) and isinstance(row.get("n"), int):
                notes[row["n"]] = row
    return notes


def _box(v):
    if not (isinstance(v, (list, tuple)) and len(v) == 4):
        return None
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(t))) for t in v)
    except (TypeError, ValueError):
        return None
    if x1 - x0 < 0.03 or y1 - y0 < 0.03:
        return None
    return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]


def _clip(s, n):
    s = " ".join(str(s or "").split())
    return s[:n]


def clean(story, page_count):
    """モデルの台本を、描画が受け付ける形に整える。壊れたコマは落とす"""
    out = {"title": _clip(story.get("title"), 30) or "資料の解説",
           "subtitle": _clip(story.get("subtitle"), 40),
           "questions": [_clip(q, 40) for q in story.get("questions", [])][:3],
           "answers": [_clip(a, 44) for a in story.get("answers", [])][:3], "scenes": []}
    used = set()
    for sc in story.get("scenes", []):
        kind = sc.get("kind")
        lines = []
        for ln in sc.get("lines", []):
            text = _clip(ln.get("text"), 60)
            if not text:
                continue
            row = {"text": text, "say": _clip(ln.get("say"), 120) or text}
            if kind == "page":
                b = _box(ln.get("box"))
                if b:
                    row["box"] = b
            if kind in ("diagram", "questions", "recap"):
                try:
                    row["reveal"] = max(0, int(ln.get("reveal", 0)))
                except (TypeError, ValueError):
                    row["reveal"] = 0
            lines.append(row)
        if not lines:
            continue
        s = {"kind": kind, "heading": _clip(sc.get("heading"), 30), "lines": lines}
        if kind == "page":
            try:
                n = int(sc.get("page"))
            except (TypeError, ValueError):
                continue
            if not 1 <= n <= page_count or n in used:
                continue
            used.add(n)
            s["page"] = n
            f = _box(sc.get("focus"))
            if f and (f[2] - f[0]) * (f[3] - f[1]) < 0.8:
                s["focus"] = f
        elif kind == "diagram":
            t = sc.get("type") if sc.get("type") in ("steps", "compare", "points") else "points"
            items = [{"label": _clip(i.get("label"), 16), "note": _clip(i.get("note"), 32)}
                     for i in sc.get("items", []) if isinstance(i, dict) and i.get("label")]
            items = items[:2] if t == "compare" else items[:5 if t == "steps" else 4]
            if len(items) < 2:
                continue
            s.update(type=t, items=items)
        elif kind in ("questions", "recap"):
            if not out["questions"]:
                continue
        elif kind != "title":
            continue
        # 足していく図は、最後の行で全部見えるようにする（言い忘れて図が欠けたまま終わらないように）
        if kind in ("diagram", "questions", "recap"):
            total = len(s.get("items") or out["questions"])
            prev = 0
            for row in lines:
                row["reveal"] = min(total, max(prev, row["reveal"] or prev))
                prev = row["reveal"]
            lines[-1]["reveal"] = total
            if lines[0]["reveal"] == 0 and len(lines) == 1:
                lines[0]["reveal"] = total
        out["scenes"].append(s)
    if not out["scenes"] or out["scenes"][0]["kind"] != "title":
        out["scenes"].insert(0, {"kind": "title", "heading": "", "lines": [{"text": out["title"], "say": out["title"]}]})
    return out


def write_story(pages, notes, title_hint=""):
    """2段目：メモから台本を組む"""
    memo = []
    for p in pages:
        m = notes.get(p["n"], {})
        fig = m.get("figure") or None
        memo.append({"n": p["n"], "role": m.get("role"), "keep": m.get("keep"), "gist": m.get("gist", ""),
                     **({"figure": fig} if fig else {})})
    parts = [{"text": "資料のメモです。" + (f"資料名：{title_hint}\n" if title_hint else "\n") +
              json.dumps(memo, ensure_ascii=False)}]
    for attempt in range(2):
        try:
            raw = _json(ask(parts, STORY_RULES, max_tokens=12000))
            story = clean(raw, len(pages))
            if len(story["scenes"]) >= 5:
                return story
            raise ValueError("too few scenes")
        except (ValueError, KeyError) as e:
            if attempt:
                raise
            print(json.dumps({"story_retry": type(e).__name__}))


def figure_boxes(notes):
    """ページ番号 → 図の box（台本が focus を書き忘れたときに寄る先）"""
    out = {}
    for n, m in notes.items():
        f = m.get("figure") if isinstance(m, dict) else None
        b = _box(f.get("box")) if isinstance(f, dict) else None
        if b:
            out[n] = b
    return out
