"""毎朝の見回り（docs/daily-watch/SKILL.md）の材料：直近の失敗とアラームを1枚の Markdown にまとめる。読み取りだけ。

  AWS_PROFILE=sandbox uv run --no-project --with boto3 python infra/scripts/daily_check.py [--hours 24]

回答は返り続けるが中の一部だけが失敗する型（道具の失敗・ログの「〜_error」・Lambda のエラー）は、
メールのアラームでは拾わない（2026-11-19 みのるん決定）。ここで内訳を出し、見回りの Claude Code が原因を調べて直す。
"""
import argparse
import datetime
import time

import boto3

REGION = "us-west-2"
JST = datetime.timezone(datetime.timedelta(hours=9))
LAMBDAS = ["interceptor", "usage-page", "turn-metrics", "turn-flag", "sessions-refresh", "x-search", "prune-images"]


def insights(logs, groups, query, start, end):
    qid = logs.start_query(logGroupNames=groups, startTime=int(start), endTime=int(end), queryString=query)["queryId"]
    for _ in range(90):
        r = logs.get_query_results(queryId=qid)
        if r["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        time.sleep(2)
    if r["status"] != "Complete":
        raise RuntimeError(f"Logs Insights が {r['status']}")
    return [{f["field"]: f["value"] for f in row if f["field"] != "@ptr"} for row in r["results"]]


def runtime_groups(control):
    out = []
    for rt in control.list_agent_runtimes(maxResults=100)["agentRuntimes"]:
        if rt["agentRuntimeName"].startswith("minorun_agent"):
            out.append(f"/aws/bedrock-agentcore/runtimes/{rt['agentRuntimeId']}-DEFAULT")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    a = ap.parse_args()
    s = boto3.Session(region_name=REGION)
    logs, cw, control = s.client("logs"), s.client("cloudwatch"), s.client("bedrock-agentcore-control")
    end = time.time()
    start = end - a.hours * 3600
    t0 = datetime.datetime.fromtimestamp(start, JST).strftime("%m/%d %H:%M")
    t1 = datetime.datetime.fromtimestamp(end, JST).strftime("%m/%d %H:%M")
    print(f"# みのるんエージェントの失敗とアラーム（{t0}〜{t1} JST）\n")

    # アラーム：今の状態と、期間中に ALARM になった記録
    alarms = [x for p in cw.get_paginator("describe_alarms").paginate()
              for x in p["MetricAlarms"] if x["AlarmName"].startswith(("みのるんエージェント", "minorun-agent"))]
    print("## アラーム\n")
    for x in alarms:
        mail = "メール" if x["AlarmActions"] else "メールなし"
        label = "" if "メールなし" in x["AlarmName"] else f"（{mail}）"
        hist = cw.describe_alarm_history(AlarmName=x["AlarmName"], HistoryItemType="StateUpdate",
                                         StartDate=datetime.datetime.fromtimestamp(start, datetime.UTC),
                                         EndDate=datetime.datetime.fromtimestamp(end, datetime.UTC))["AlarmHistoryItems"]
        fired = sum(1 for h in hist if "to ALARM" in h["HistorySummary"])
        print(f"- {x['AlarmName']}{label}：いま {x['StateValue']}、期間中に ALARM {fired}回")
    print()

    # 道具の失敗（トレース）
    print("## 道具の失敗（aws/spans の execute_tool の ERROR）\n")
    rows = insights(logs, ["aws/spans"],
                    'filter name like /^execute_tool / and status.code = "ERROR" and resource.attributes.service.name like /^minorun_agent/ '
                    '| stats count() as n, max(@timestamp) as last by name, status.message | sort n desc | limit 30', start, end)
    print("\n".join(f"- {r.get('name')}：{r.get('n')}件（最後 {r.get('last')} UTC）{r.get('status.message', '')}" for r in rows) or "- なし")
    print()

    # ログの「〜_error」
    print("## ログの「〜_error」（Runtime とスライド作成機能）\n")
    groups = runtime_groups(control)
    rows = insights(logs, groups,
                    'filter @message like /_error": / | parse @message /"(?<key>[a-z_]+_error)": *"?(?<val>[^",}]*)/ '
                    '| stats count() as n, max(@timestamp) as last by key, val | sort n desc | limit 30', start, end)
    print("\n".join(f"- {r.get('key')} = {r.get('val')}：{r.get('n')}件（最後 {r.get('last')} UTC）" for r in rows) or "- なし")
    print()

    # Lambda のエラー
    print("## Lambda のエラー\n")
    found = False
    for name in LAMBDAS:
        fn = f"minorun-agent-{name}"
        res = cw.get_metric_statistics(Namespace="AWS/Lambda", MetricName="Errors", Dimensions=[{"Name": "FunctionName", "Value": fn}],
                                       StartTime=datetime.datetime.fromtimestamp(start, datetime.UTC),
                                       EndTime=datetime.datetime.fromtimestamp(end, datetime.UTC), Period=int(a.hours * 3600), Statistics=["Sum"])
        n = int(sum(p["Sum"] for p in res["Datapoints"]))
        if n:
            found = True
            print(f"- {fn}：{n}件（ロググループ /aws/lambda/{fn} の [ERROR] と Status: timeout を見る）")
    if not found:
        print("- なし")
    print()


if __name__ == "__main__":
    main()
