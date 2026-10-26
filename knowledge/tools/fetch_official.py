"""re:Invent 公式サイト（aws.amazon.com/events/reinvent/ 配下）をたどって本文を sources/cache/off-<id>.txt に取る。

前回から本文が変わったページだけを sources/batches/official-<今日>.txt に並べるので、
会期中の更新はそのリストのページだけ読み直せばよい（読んだ結果は sources/official/<id>.json）。
使い方: python3 tools/fetch_official.py
"""
import datetime, hashlib, json, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import fetch_bodies as fb  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/130 Safari/537.36"}
SITE = "https://aws.amazon.com"
START = ["/events/reinvent/", "/events/reinvent/faqs/", "/events/reinvent/know-before-you-go/"]

def get(u):
    r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30)
    return r.geturl(), r.read().decode("utf-8", "ignore")

def main():
    seen, queue, changed = set(), list(START), []
    while queue and len(seen) < 80:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        try:
            url, h = get(SITE + path)
        except Exception as e:
            print("fail", path, e, file=sys.stderr); continue
        if not url.startswith(SITE + "/events/reinvent"):
            continue
        for l in set(re.findall(r'href="(?:https://aws\.amazon\.com)?(/events/reinvent/[^"#?]*)"', h)):
            if l not in seen and not re.search(r"\.(css|js|png|svg|pdf)$", l):
                queue.append(l)
        text = fb.strip(h)
        if len(text) < 500:  # JavaScript で描くページ（カレンダーなど）は本文が取れない
            continue
        m = re.search(r"<title>(.*?)</title>", h, re.S)
        i = "off-" + hashlib.sha1(url.encode()).hexdigest()[:10]
        p = ROOT / "sources" / "cache" / f"{i}.txt"
        body = (f"URL: {url}\nTITLE: {(m.group(1).strip() if m else '')}\n"
                f"SITE: re:Invent 公式サイト（aws.amazon.com）\nAUTHOR: AWS\nPUBLISHED: \n\n{text}")
        if not p.exists() or p.read_text() != body:
            p.write_text(body); changed.append(i)
    out = ROOT / "sources" / "batches" / f"official-{datetime.date.today().isoformat()}.txt"
    if changed:
        out.write_text("\n".join(changed) + "\n")
    print(f"{len(seen)}ページを見て、変わったページ{len(changed)}件" + (f" → {out.name}" if changed else ""))

if __name__ == "__main__":
    main()
