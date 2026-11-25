"""スライド作成機能の通しテスト（2026-11-21）。スライド作成の Runtime を直接起こし、本番と同じ置き場に1本作らせて、できたものを確かめる。

  uv run --with boto3 python tests/e2e/slides_run.py --runtime <スライド作成の Runtime の ARN> [--case article|topic|all]

回答のエージェント（make_slides）を通さないので、回数の表は数えない（counted_day を渡さない）。
確かめること：status が done になる・ビューア・PPTX・表紙の画像がある・ページ数が10〜24・
スライド作成機能のログの最後の行が ok で、Harness で書いた（engine=harness）・図や挿絵を入れたなら焼いた画像に写っている数。
終了コードは NG があれば 1。作ったスライドの URL は標準出力に出す（目で見るため）。
"""
import argparse
import json
import secrets
import sys
import time

import boto3

REGION = "us-west-2"
SITE = "https://agent.minoruonda.com"

CASES = {
    # 構成が書いてある記事（図を描く場面がある）
    "article": {"url": "https://github.com/minorun365/minorun-agent",
                "ask": "アーキテクチャがひと目で分かるようにしてほしい"},
    # 調べてまとめるテーマ（材料は要点と出典）
    "topic": {"topic": "Amazon Bedrock AgentCore の Harness でエージェントを動かす",
              "notes": ("- AgentCore Harness は、モデル・道具・スキル・指示を設定で宣言すると、エージェントの繰り返しを AgentCore が動かす仕組み（2026-06 に一般提供）\n"
                        "- 中身は Strands Agents。セッションごとに分かれた microVM（AgentCore Runtime）で動き、シェルとファイルの道具を持つ\n"
                        "- スキルは SKILL.md 形式で、AWS Skills・Git・S3・コンテナ内のパスから読み込める。説明だけを先に読み、本文は要るときに開く\n"
                        "- モデルは Bedrock・OpenAI・Gemini・LiteLLM 経由のプロバイダを選べ、呼び出しごとに替えられる。同じセッションのまま替えても会話は続く\n"
                        "- Harness そのものに追加料金は無く、下で動く Runtime とモデルの分がかかる"),
              "sources": ["https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html",
                          "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-skills.html"],
              "ask": "初めての人向けに"},
}

s3 = boto3.client("s3", region_name=REGION)
# 置き場の名前はアカウント番号つき（infra/lib/slides-stack.ts）。番号はリポジトリに書かず、いまの認証から引く
BUCKET = f"minorun-agent-slides-{boto3.client('sts').get_caller_identity()['Account']}"
logs = boto3.client("logs", region_name=REGION)
agentcore = boto3.client("bedrock-agentcore", region_name=REGION)


def status(job):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=f"s/{job}/status.json")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def final_log(runtime_id, job, since_ms):
    group = f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"
    lines = []
    kwargs = {"logGroupName": group, "startTime": since_ms, "filterPattern": f'"{job}"'}
    while True:
        page = logs.filter_log_events(**kwargs)
        lines += [e["message"] for e in page["events"]]
        if not page.get("nextToken"):
            break
        kwargs["nextToken"] = page["nextToken"]
    recs = []
    for line in lines:
        try:
            recs.append(json.loads(line[line.index("{"):]))
        except Exception:
            pass
    return recs


def run(runtime, name, case, timeout):
    job = secrets.token_urlsafe(12).replace("-", "a").replace("_", "b")
    started = int(time.time() * 1000)
    s3.put_object(Bucket=BUCKET, Key=f"s/{job}/status.json", ContentType="application/json", CacheControl="no-cache, max-age=0",
                  Body=json.dumps({"stage": "queued", "kind": "topic" if "topic" in case else "article", "at": int(time.time())}).encode())
    body = {"job": job, "user": "e2e", **case}
    # ARN の「/」をそのまま送るとプロキシ越しで 404 になる環境があるので、ID とアカウントで呼ぶ（API の決まりで同じ意味）
    arn = runtime.split(":")
    agentcore.invoke_agent_runtime(agentRuntimeArn=arn[5].split("/", 1)[1], accountId=arn[4], qualifier="DEFAULT",
                                   runtimeSessionId=f"slides-e2e-{job}-{secrets.token_hex(8)}",
                                   contentType="application/json", accept="application/json",
                                   payload=json.dumps(body, ensure_ascii=False).encode())["response"].read()
    print(f"[{name}] job={job}", flush=True)
    last, st = None, None
    while time.time() * 1000 - started < timeout * 1000:
        st = status(job)
        stage = (st or {}).get("stage")
        now = (stage, (st or {}).get("detail"))
        if now != last:
            print(f"[{name}] {round(time.time() - started / 1000)}s {stage} {now[1] or ''}", flush=True)
            last = now
        if stage in ("done", "error"):
            break
        time.sleep(10)
    ng = []
    if not st or st.get("stage") != "done":
        ng.append(f"done にならなかった：{st}")
        return job, ng, {}
    for key in ("index.html", "deck.pptx", "og.jpg", "01.webp", "deck.md"):
        try:
            s3.head_object(Bucket=BUCKET, Key=f"s/{job}/{key}")
        except Exception:
            ng.append(f"{key} が無い")
    pages = st.get("pages") or 0
    if not 10 <= pages <= 24:
        ng.append(f"ページ数が {pages}")
    md = s3.get_object(Bucket=BUCKET, Key=f"s/{job}/deck.md")["Body"].read().decode()
    # 表紙の題の下の1行は短い句1つ（全角15字まで。英数字は2字で全角1字）。長いと表紙が説明文で埋まり、右の飾りの輪にもかかる
    cover = md.split("\n---\n")[1] if md.startswith("---") else md.split("\n---\n")[0]
    tail = [ln.strip() for ln in cover.strip().splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "<!--"))]
    if tail and sum(0.5 if c.isascii() else 1 for c in tail[-1]) > 16:
        ng.append(f"表紙の題の下の1行が長い：{tail[-1]}")
    runtime_id = runtime.rsplit("/", 1)[-1]
    time.sleep(15)
    recs = final_log(runtime_id, job, started)
    final = [r for r in recs if r.get("job") == job and "ok" in r]
    engine = [r for r in recs if r.get("engine") == "harness"]
    route = [r for r in recs if "credit_route" in r]
    if not final or not final[-1].get("ok"):
        ng.append("最後のログが ok ではない")
    if not engine:
        ng.append("Harness で書いていない（従来の書き方に落ちた）")
    info = {"viewer": f"{SITE}/s/{job}/", "pages": pages, "figs": md.count('class="fig"'), "ills": md.count('class="ill"'),
            "route": route[-1] if route else None, "harness": engine[-1] if engine else None,
            "ng_left": final[-1].get("ng_left") if final else None, "seconds": final[-1].get("seconds") if final else None,
            "usage": final[-1].get("usage") if final else None}
    if engine and engine[-1].get("ng"):
        ng.append(f"焼き直し後も崩れが {engine[-1]['ng']} 枚")
    if engine and not engine[-1].get("ills"):
        ng.append("挿絵が1枚も入っていない")
    if engine and engine[-1].get("dropped"):
        ng.append(f"消された画像がある：{engine[-1]['dropped']}")
    return job, ng, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--case", default="all")
    ap.add_argument("--timeout", type=int, default=2700)
    ap.add_argument("--url", help="article の記事を差し替える（公開元の表記を確かめるときなど）")
    a = ap.parse_args()
    names = list(CASES) if a.case == "all" else [a.case]
    bad = 0
    for name in names:
        case = dict(CASES[name], url=a.url, ask="") if a.url and name == "article" else CASES[name]
        job, ng, info = run(a.runtime, name, case, a.timeout)
        print(json.dumps({"case": name, "job": job, "ng": ng, **info}, ensure_ascii=False, indent=1))
        bad += bool(ng)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
