"""1問1行の表（minorun-agent-conversations）に行が足されるたびに、その行をメトリクスにする。

エージェントは「最初の文字までの時間」を表にだけ書いてログに出さないので、表のストリームから拾う。
出し方は EMF（ログに決まった形の JSON を書くと CloudWatch がメトリクスにする）。本文は書かない。
"""

import json
import time

NAMESPACE = "MinorunAgent"


def _num(image, key):
    value = image.get(key, {})
    return float(value["N"]) if "N" in value else None


def handler(event, _context):
    for record in event.get("Records", []):
        if record.get("eventName") != "INSERT":
            continue
        image = record["dynamodb"].get("NewImage", {})
        metrics = {"Questions": 1}
        for key, name in (("first_ms", "FirstTextMs"), ("total_ms", "TotalMs")):
            value = _num(image, key)
            if value is not None:
                metrics[name] = value
        catalog = image.get("catalog", {}).get("S")
        if catalog == "changed":
            metrics["CatalogChanged"] = 1
        units = {"Questions": "Count", "CatalogChanged": "Count"}
        print(json.dumps({
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": NAMESPACE,
                    "Dimensions": [[]],
                    "Metrics": [{"Name": k, "Unit": units.get(k, "Milliseconds")} for k in metrics],
                }],
            },
            **metrics,
        }))
