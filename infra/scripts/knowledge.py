"""知識ベースの取り込みと、年の絞り込みの確かめ。

  uv run --with boto3 python infra/scripts/knowledge.py ingest   # S3 の kb/ を取り込み直して終わるまで待つ
  uv run --with boto3 python infra/scripts/knowledge.py check    # 質問20問を year=2026 / year<2026 で引き、年の混ざりを数える
  uv run --with boto3 python infra/scripts/knowledge.py sessions # セッションカタログの控えを今すぐ取り直す
S3 への書き出しは CDK（MinorunKnowledge の BucketDeployment）が行う。先に `python3 knowledge/tools/build_kb.py` と deploy を済ませる。
"""

import json
import sys
import time

import boto3

REGION = "us-west-2"
session = boto3.Session(region_name=REGION)
agent = session.client("bedrock-agent")
runtime = session.client("bedrock-agent-runtime")

QUESTIONS = [
    "バッジはどこで受け取る？", "基調講演はいつ？", "会場間のシャトルバスはある？", "Expo は何時から？",
    "re:Play はいつどこで？", "ラスベガスの空港から会場への行き方", "おすすめのホテルの選び方", "持っていくべきもの",
    "日本人の交流会はある？", "朝ごはんはどこで食べられる？", "Wi-Fi やモバイル回線はどうする？", "治安は大丈夫？",
    "セッションの予約のしかた", "ジャムや GameDay とは？", "グッズ（Swag）はどこでもらえる？", "ESTA の申請",
    "チップの払い方", "re:Invent に参加する心構え", "会場の移動にかかる時間", "Japan Tour とは？",
]


def outputs():
    # cdkd は CloudFormation のスタックを作らないので、出力値は名前から引く
    kb = next(k for k in agent.list_knowledge_bases()["knowledgeBaseSummaries"] if k["name"] == "minorun-agent-kb")
    ds = next(d for d in agent.list_data_sources(knowledgeBaseId=kb["knowledgeBaseId"])["dataSourceSummaries"]
              if d["name"] == "minorun-agent-kb-s3")
    return {"KnowledgeBaseId": kb["knowledgeBaseId"], "DataSourceId": ds["dataSourceId"]}


def ingest(out):
    kb, ds = out["KnowledgeBaseId"], out["DataSourceId"]
    for _ in range(60):
        if agent.get_data_source(knowledgeBaseId=kb, dataSourceId=ds)["dataSource"]["status"] == "AVAILABLE":
            break
        time.sleep(10)
    job = agent.start_ingestion_job(knowledgeBaseId=kb, dataSourceId=ds)["ingestionJob"]
    started = time.time()
    while True:
        job = agent.get_ingestion_job(knowledgeBaseId=kb, dataSourceId=ds,
                                      ingestionJobId=job["ingestionJobId"])["ingestionJob"]
        if job["status"] in ("COMPLETE", "FAILED", "STOPPED"):
            break
        time.sleep(15)
    print(json.dumps({"status": job["status"], "seconds": round(time.time() - started),
                      "statistics": job.get("statistics"), "failureReasons": job.get("failureReasons")},
                     ensure_ascii=False, default=str, indent=1))


def retrieve(kb, text, flt):
    res = runtime.retrieve(knowledgeBaseId=kb, retrievalQuery={"text": text},
                           retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": 8, "filter": flt}})
    return res["retrievalResults"]


def check(out):
    kb = out["KnowledgeBaseId"]
    bad, empty, rows = 0, 0, []
    for q in QUESTIONS:
        now = retrieve(kb, q, {"equals": {"key": "year", "value": 2026}})
        past = retrieve(kb, q, {"lessThan": {"key": "year", "value": 2026}})
        # 欠けた付箋を0年として扱うと、過去側の検索が誤って合格する。
        years_now = [int(r["metadata"]["year"]) for r in now]
        years_past = [int(r["metadata"]["year"]) for r in past]
        if any(y < 2010 for y in years_now + years_past):
            raise ValueError(f"年の付箋が不正: {q}")
        bad += sum(y != 2026 for y in years_now) + sum(y >= 2026 for y in years_past)
        empty += not now
        top = (now[0]["content"]["text"] if now else "").replace("\n", " ")[:70]
        rows.append({"q": q, "2026": len(now), "past": len(past), "past_years": sorted(set(years_past)), "top": top})
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    print(f"年の混ざり {bad} 件 ／ 2026年が0件だった質問 {empty} 問 ／ {len(QUESTIONS)} 問")
    if bad or empty:
        raise RuntimeError("知識ベースの検索検査に失敗")


def sessions():
    res = session.client("lambda").invoke(FunctionName="minorun-agent-sessions-refresh",
                                          Payload=json.dumps({"force": True}).encode())
    print(res["Payload"].read().decode())


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "sessions":
        sessions()
    else:
        {"ingest": ingest, "check": check}[cmd](outputs())
