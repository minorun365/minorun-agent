"""入口の検証：Gateway（または CloudFront の /api/chat）へ送り、返事が届く間隔を測る。

  uv run python infra/scripts/probe_entry.py stream [--url URL]      # SSE が少しずつ届くか
  uv run python infra/scripts/probe_entry.py burst  [--url URL] -n 7 # 同じ匿名IDで続けて送る
  uv run python infra/scripts/probe_entry.py badid  [--url URL]      # 不正な匿名ID
URL を省くと環境変数 ENTRY_URL（https://<独自ドメイン>/api/chat）を使う（WAF・CloudFront・Gateway・確認役を全部通る）。POST は urllib で打つ。
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid


def default_url():
    return os.environ["ENTRY_URL"]


def send(url, anon_id, prompt="こんにちは", quiet=False):
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps({"prompt": prompt}).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream", "x-anon-id": anon_id},
    )
    t0 = time.time()
    try:
        res = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        return {"status": e.code, "body": body, "first_ms": None, "chunks": []}
    chunks = []
    while True:
        line = res.readline()
        if not line:
            break
        text = line.decode("utf-8", "ignore").strip()
        if text:
            chunks.append((round((time.time() - t0) * 1000), text[:90]))
            if not quiet:
                print(f"  [{chunks[-1][0]:>5}ms] {chunks[-1][1]}")
    return {"status": res.status, "content_type": res.headers.get("Content-Type"),
            "first_ms": chunks[0][0] if chunks else None, "chunks": chunks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["stream", "burst", "badid"])
    ap.add_argument("--url", default=None)
    ap.add_argument("-n", type=int, default=7)
    a = ap.parse_args()
    url = a.url or default_url()
    print("url", url)

    if a.mode == "stream":
        r = send(url, str(uuid.uuid5(uuid.NAMESPACE_URL, "minorun-agent:test:" + a.mode)))
        times = [c[0] for c in r["chunks"]]
        gaps = [b - a for a, b in zip(times, times[1:])]
        print(json.dumps({"status": r["status"], "content_type": r.get("content_type"), "first_ms": r["first_ms"],
                          "chunks": len(times), "gaps_ms": gaps, "body": r.get("body")}, ensure_ascii=False))
    elif a.mode == "burst":
        anon = str(uuid.uuid5(uuid.NAMESPACE_URL, "minorun-agent:test:" + a.mode))
        for i in range(a.n):
            r = send(url, anon, quiet=True)
            print(i + 1, r["status"], r.get("body") or f"{len(r['chunks'])} chunks")
    else:
        r = send(url, "bad id!")
        print(r["status"], r.get("body"))


if __name__ == "__main__":
    main()
