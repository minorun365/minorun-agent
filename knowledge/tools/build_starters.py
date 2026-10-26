"""画面が最初に出す質問の例（knowledge/starters.json）を、ナレッジベースの中身から作り直す。

話題ごとに事実の指紋（全文のハッシュ）を持ち、事実が増えた・変わった話題だけを Haiku 5.5 で作り直す。
材料は新しく取り込んだ事実を優先する（取得日の新しい順に30件＋残りから10件をランダム）ので、
拡充した分がそのまま質問の例に出る。変わっていない話題は Bedrock を呼ばない。
ナレッジベースに入る事実は build_kb.py と同じものを使う（load() を共有）。
画面（web/src/starters.ts）はこのファイルをビルド時に読み込み、毎回別々の話題から3つ選ぶ。
使い方: AWS_PROFILE=sandbox uv run --with boto3 python knowledge/tools/build_starters.py [--force] [話題...]
"""
import collections, hashlib, json, random, re, sys
from pathlib import Path

import boto3

from build_kb import ROOT, TOPICS, load

OUT = ROOT / "starters.json"
MODEL = "global.anthropic.claude-haiku-5-5"
PER_TOPIC = 6
bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")

PROMPT = """あなたは「みのるんエージェント」（re:Invent に日本から参加する人向けの Q&A）の画面に出す、質問の例を作る係です。
話題「{label}」について、下の事実だけで答えられる質問を{n}個作ります。利用者が押したくなる、具体的で短い問いにします。

# 質問の決まり
- 22字以内の話し言葉の疑問文で、最後は「？」。「教えて」「ですか」「ますか」は使わない
- 1つの問いに1つのことだけを聞く。同じことを言い換えた問いを並べない
- 初めて参加する人が実際に困りそうなこと・知りたいことを選ぶ。経験者が新しい発見を得られる問いも混ぜる
- 「おすすめは？」「注意点は？」だけで済ませず、サービス名・セッション形式・参加目的・具体的な場面のどれかを入れる。同じ話題でも比較・選び方・体験できることを使い分ける
- 材料にサービスの更新があれば、何ができるようになったかを聞く。取得日が新しいことを発表が新しいことと混同しない
- Jam・GameDay・チョークトークなどの材料があれば、定義だけでなく参加目的に合う形式の選び方も聞く。1人の体験談の細部や小ネタは問いにしない
- 事実に無いことを前提にしない。年で変わる話は「2026年は」と決めつけない
- 事実の先頭のものほど新しく入った情報なので、少なくとも半分はそこから作る
- 次の問いはすでに画面にあるので、同じ問いを作らない：{taken}

出力は JSON の配列だけ（コードブロックにしない）：["…？", "…？"]

# 事実
{facts}"""

BAD = re.compile(r"教えて|ですか|ますか|りる？|る？る？")  # tests/web/starters.test.ts と同じ規則


def ok(q):
    return q.endswith(("？", "?")) and len(q) <= 22 and not BAD.search(q)


def groups():
    g = collections.defaultdict(list)
    for _, d in load():
        for f in d.get("facts", []):
            if f.get("year", d.get("event_year")):
                g[f["topic"]].append((d.get("retrieved") or "", f["text"]))
    return g


def ask(topic, rows, taken):
    rows = sorted(rows, reverse=True)
    pick = rows[:30] + random.sample(rows[30:], min(10, len(rows[30:])))
    text = PROMPT.format(label=TOPICS[topic], n=PER_TOPIC + 2, taken="、".join(sorted(taken)[:80]) or "なし",
                         facts="\n".join(f"- {t}" for _, t in pick))
    for _ in range(2):  # 考える分で上限を使い切り、本文が空になることがある（2,000では13話題/37で起きた）
        r = bedrock.converse(modelId=MODEL, messages=[{"role": "user", "content": [{"text": text}]}],
                             inferenceConfig={"maxTokens": 8000},
                             additionalModelRequestFields={"output_config": {"effort": "low"}})
        out = "".join(c.get("text", "") for c in r["output"]["message"]["content"])
        if "[" in out:
            break
    qs = json.loads(out[out.find("["): out.rfind("]") + 1])
    keep = []
    for q in qs:
        q = str(q).strip().replace("?", "？")
        if ok(q) and q not in taken and q not in keep:
            keep.append(q)
    return keep[:PER_TOPIC]


def main():
    force = "--force" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    new, changed = {}, []
    for topic, rows in sorted(groups().items()):
        h = hashlib.sha256((PROMPT + "\n" + "\n".join(sorted(t for _, t in rows))).encode()).hexdigest()[:16]
        prev = old.get(topic)
        if prev and prev["hash"] == h and not force and topic not in only:
            new[topic] = prev
            continue
        if only and topic not in only:
            if prev:
                new[topic] = prev
            continue
        taken = {q for k, v in {**old, **new}.items() if k != topic for q in v["questions"]}
        try:
            qs = ask(topic, rows, taken)
        except Exception as e:  # 作り直しに失敗しても前の質問を残す
            print(f"{topic}: 失敗 {type(e).__name__}: {str(e)[:200]}（前の質問を残す）")
            if prev:
                new[topic] = prev
            continue
        new[topic] = {"hash": h, "facts": len(rows), "questions": qs}
        changed.append(f"{topic}（{len(rows)}件）: {' / '.join(qs)}")
    # ナレッジベースから消えた話題は落とす
    OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n")
    print(f"作り直した話題 {len(changed)} ／ 全 {len(new)} 話題・{sum(len(v['questions']) for v in new.values())} 問")
    for c in changed:
        print(" ", c)


if __name__ == "__main__":
    main()
