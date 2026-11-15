"""スライド工房（Lambda）。URL の記事から minorun-glass テーマの解説スライドを作り、めくれるビューアとして公開する。

エージェントのツール make_slides がジョブを受け付け、この関数を非同期で起動する（画面までの入口は60秒で切れるため）。
進み具合は公開用 S3 の s/<ジョブ>/status.json に書き、画面が数秒おきに読みに来る。
  reading → writing → rendering → checking（直すときだけ fixing）→ done / error

作り方は personal の常時稼働エージェントのデッキ（2026-11-14）で効いた部品をそのまま持ち込んだ：
テーマの型・見出しの文体と書き方の規則・余白の実測。実測で崩れたスライドがあれば1回だけ直させて焼き直す。
"""

import asyncio
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

import boto3
from botocore.config import Config
from PIL import Image
from pptx import Presentation
from pptx.util import Emu

from url_reader import read_url

REGION = os.environ.get("AWS_REGION", "us-west-2")
BUCKET = os.environ["SLIDES_BUCKET"]
SITE = os.environ.get("SITE_URL", "https://agent.minoruonda.com").rstrip("/")
MODEL = os.environ.get("SLIDES_MODEL", "global.anthropic.claude-opus-5-5")
EFFORT = os.environ.get("SLIDES_EFFORT", "medium")
HERE = Path(__file__).parent
THEME = HERE / "theme" / "minorun-glass.css"

s3 = boto3.client("s3", region_name=REGION)
# 1回の書き上げに数分かかることがある。既定の読み取り待ち（60秒）では切れる
bedrock = boto3.client("bedrock-runtime", region_name=REGION,
                       config=Config(read_timeout=600, connect_timeout=10, retries={"max_attempts": 2}))

RULES = """（公開版では省略しています。スライドの書き方の指示は各自で用意してください。slides/core/README.md）
"""


# 調べてまとめるとき（記事ではなく、エージェントが検索で確かめた要点と出典から作る）。RULES の「記事」は「材料」と読み替えさせ、構成だけを差し替える
TOPIC_RULES = RULES.split("# 構成")[0] + """（公開版では省略しています。スライドの書き方の指示は各自で用意してください。slides/core/README.md）
"""


KIND = {"value": "article"}  # 1回の起動の間だけ持つ。画面が「記事を読む」と「出典を読む」を言い分ける


def status(job, stage, **extra):
    body = json.dumps({"stage": stage, "kind": KIND["value"], "at": int(time.time()), **extra}, ensure_ascii=False)
    s3.put_object(Bucket=BUCKET, Key=f"s/{job}/status.json", Body=body.encode(),
                  ContentType="application/json", CacheControl="no-cache, max-age=0")


def ask_model(messages, rules=RULES):
    res = bedrock.converse(
        modelId=MODEL, system=[{"text": rules}], messages=messages,
        inferenceConfig={"maxTokens": 16000},
        additionalModelRequestFields={"output_config": {"effort": EFFORT}})
    text = "".join(c.get("text", "") for c in res["output"]["message"]["content"])
    u = res.get("usage", {})
    return text, {"input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0)}


ALLOWED = {"div", "span", "b", "br", "mark", "p", "small"}


def clean(md):
    """モデルの書いた Markdown を、テーマの型だけに絞る（記事由来の HTML・画像・スタイルを残さない）。"""
    md = md.strip()
    md = re.sub(r"^```[a-z]*\n|\n```$", "", md)
    md = re.sub(r"\A---\n.*?\n---\n", "", md, flags=re.S)  # フロントマターを書いてきたら外す
    md = re.sub(r"<(script|style|iframe|object|embed|svg|math)\b.*?</\1\s*>", "", md, flags=re.S | re.I)

    def tag(m):
        name = m.group(2).lower()
        if name not in ALLOWED:
            return ""
        if m.group(1):
            return f"</{name}>"
        cls = re.search(r'class="(steps|flow|prompt|point)"', m.group(0))
        return f'<{name} class="{cls.group(1)}">' if cls else f"<{name}>"
    md = re.sub(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", tag, md)
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)  # 画像は使わない
    md = md.replace("**", "").replace("—", "、").replace("──", "、")
    return md.strip()


def sources_slide(urls):
    """出典の一覧のスライド（調べてまとめたときだけ）。モデルに書かせると URL が大きな青い字で折り返すので、決まった体裁で足す"""
    if not urls:
        return ""
    rows = "\n".join(f"<li><b>{html.escape(urllib.parse.urlparse(u).netloc)}</b><small>{html.escape(u)}</small></li>" for u in urls)
    return ("\n\n---\n\n<style scoped>/*src*/ol{margin:8px 0 0;padding-left:1.4em}"
            "li{margin:0 0 16px;font-size:24px;line-height:1.4}li b{display:block;font-weight:700}"
            "li small{display:block;font-size:17px;color:var(--mute,#647b88);word-break:break-all;line-height:1.45;margin-right:200px}</style>\n\n"
            f"# 出典\n\n<ol>\n{rows}\n</ol>")


def deck(md, domain, sources=()):
    front = f"---\nmarp: true\nlang: ja\npaginate: true\ntheme: minorun-glass\nheader: '{domain}'\n---\n\n"
    end = ("\n\n---\n\n<!-- _class: end -->\n<!-- _paginate: false -->\n\n# Thank you!\n\n"
           "みのるんエージェント<small>agent.minoruonda.com</small>\n")
    return front + md + sources_slide(list(sources)) + end


FIT = re.compile(r"\n?<style scoped>/\*fit\*/.*?</style>\n?", re.S)
OVER = ("下にはみ出している", "右にはみ出している", "横にはみ出している要素がある")


def shrink(md, nums, z):
    """指定したスライドだけ、見出し以外の中身を z 倍に縮める。前に縮めた指定は置き換える（掛け算で縮みすぎない）"""
    head, sep, body = md.partition("\n---\n")
    parts = body.split("\n---\n")
    for n in nums:
        if 0 < n <= len(parts):
            parts[n - 1] = (f"\n<style scoped>/*fit*/section>:not(h1):not(header):not(footer){{zoom:{z}}}</style>\n"
                            + FIT.sub("\n", parts[n - 1]))
    return head + sep + "\n---\n".join(parts)


def shrink_to_fit(md, result, work):
    """モデルに直させても余白からはみ出したスライドは、中身を段階的に縮めて収める。文字は削らない"""
    for z in (0.9, 0.8, 0.72):
        targets = [r["n"] for r in result["ng"] if any(b in OVER for b in r["bad"])]
        if not targets:
            break
        md = shrink(md, targets, z)
        result = render(md, work)
        print(json.dumps({"shrink": z, "pages": targets, "ng_left": len(result["ng"])}))
    return md, result


def render(md, work):
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / "deck.md").write_text(md)
    subprocess.run(["marp", "--no-stdin", "--html", "--template", "bare", "--theme", str(THEME),
                    "--allow-local-files", "-o", str(work / "deck.html"), str(work / "deck.md")],
                   check=True, capture_output=True, timeout=120, env={**os.environ, "HOME": "/tmp"})
    out = subprocess.run(["node", str(HERE / "render.mjs"), str(work / "deck.html"), str(work / "png")],
                         check=True, capture_output=True, text=True, timeout=240, env={**os.environ, "HOME": "/tmp"})
    return json.loads(out.stdout.strip().splitlines()[-1])


def read_with_browser(url):
    """普通の取得で読めなかったときの2番手。AgentCore Browser で開き、JavaScript で組まれるページや大きいページも読む。"""
    from bedrock_agentcore.tools.browser_client import BrowserClient
    client = BrowserClient(REGION)
    try:
        client.start(session_timeout_seconds=300)
        ws_url, headers = client.generate_ws_headers()
        out = subprocess.run(["node", str(HERE / "read_browser.mjs"), url], capture_output=True, text=True, timeout=90,
                             env={**os.environ, "WS_URL": ws_url, "WS_HEADERS": json.dumps(headers)})
        if out.returncode != 0:
            print(json.dumps({"browser_read": "failed", "stderr": out.stderr[-800:]}))
            return None
        got = json.loads(out.stdout.strip().splitlines()[-1])
        print(json.dumps({"browser_read": "ok", "chars": len(got.get("text", ""))}))
        return {"status": "ok", "url": got.get("url") or url, "page_text": got.get("text", "")}
    except Exception as e:  # 2番手なので、失敗しても元の「読めませんでした」へ戻す
        print(json.dumps({"browser_read": "error", "error": type(e).__name__, "detail": str(e)[:300]}))
        return None
    finally:
        try:
            client.stop()
        except Exception:
            pass


def fix_cover(md):
    """表紙の題を h1 にそろえる。モデルが題まで ## で書いたときは、表紙の最後の ## を # に直す。"""
    parts = md.split("\n---\n", 1)
    head = parts[0]
    if "_class: top" in head and not re.search(r"^# ", head, re.M):
        subs = list(re.finditer(r"^## (.+)$", head, re.M))
        if len(subs) >= 2:
            last = subs[-1]
            head = head[:last.start()] + "# " + last.group(1) + head[last.end():]
    return "\n---\n".join([head] + parts[1:])


def title_of(md):
    """表紙（最初のスライド）の h1 を題にする。見つからなければ一般名にする。"""
    for chunk in md.split("\n---\n"):
        if "_class: top" in chunk:
            m = re.search(r"^# (.+)$", chunk, re.M)
            return re.sub(r"<br\s*/?>", "", m.group(1)).strip()[:80] if m else "スライド"
    return "スライド"


VIEWER = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>{title}｜みのるんエージェント</title>
<meta property="og:type" content="website">
<meta property="og:title" content="{title}">
<meta property="og:description" content="みのるんエージェントが{made}解説スライド（全{pages}ページ）">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{og}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{og}">
<style>
:root{{--bg:#eef7fa;--ink:#16323f;--mute:#5b707c;--btn:#ffffff;--line:#d6e6ed;--accent:#0891b2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d1b24;--ink:#e6f1f5;--mute:#9db0bb;--btn:#16303d;--line:#24414f}}}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;background:var(--bg);color:var(--ink);font-family:"Hiragino Sans","Noto Sans JP",sans-serif;display:flex;flex-direction:column;align-items:center}}
header{{width:100%;max-width:1100px;padding:18px 16px 10px;display:flex;align-items:center;gap:12px}}
header h1{{flex:1;margin:0;font-size:17px;line-height:1.4;font-weight:700}}
.share,.save{{flex:none;display:inline-flex;align-items:center;gap:6px;background:#0f1419;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:9px 16px;border-radius:999px;white-space:nowrap}}
.save{{background:var(--btn);color:var(--ink);border:1px solid var(--line)}}
@media (max-width:520px){{header{{flex-wrap:wrap}}header h1{{flex-basis:100%}}}}
.stage{{position:relative;width:100%;max-width:1100px;padding:0 16px}}
.stage img{{display:block;width:100%;height:auto;aspect-ratio:16/9;border-radius:12px;box-shadow:0 14px 40px rgba(8,100,130,.18);background:#f1fafc;user-select:none;-webkit-user-drag:none}}
.nav{{display:flex;align-items:center;justify-content:center;gap:18px;padding:16px}}
.nav button{{width:52px;height:52px;border-radius:50%;border:1px solid var(--line);background:var(--btn);color:var(--ink);font-size:22px;cursor:pointer}}
.nav button:disabled{{opacity:.35;cursor:default}}
.count{{min-width:70px;text-align:center;font-variant-numeric:tabular-nums;color:var(--mute)}}
footer{{padding:6px 16px 28px;font-size:13px;color:var(--mute);text-align:center;line-height:1.7}}
footer a{{color:var(--accent)}}
</style>
</head>
<body>
<header><h1>{title}</h1>{save}<a class="share" href="{share}" target="_blank" rel="noopener">Xでシェア</a></header>
<div class="stage"><img id="slide" src="01.webp" alt="1ページ目"></div>
<div class="nav"><button id="prev" aria-label="前へ">&#8592;</button><span class="count" id="count">1 / {pages}</span><button id="next" aria-label="次へ">&#8594;</button></div>
<footer>{footer}</footer>
<script>
const N={pages};let i=1;const img=document.getElementById('slide'),count=document.getElementById('count');
const name=(n)=>String(n).padStart(2,'0')+'.webp';
function show(n){{i=Math.min(N,Math.max(1,n));img.src=name(i);img.alt=i+'ページ目';count.textContent=i+' / '+N;
document.getElementById('prev').disabled=i===1;document.getElementById('next').disabled=i===N;
if(i<N){{const p=new Image();p.src=name(i+1);}}}}
document.getElementById('prev').onclick=()=>show(i-1);document.getElementById('next').onclick=()=>show(i+1);
img.onclick=(e)=>show(e.offsetX<img.clientWidth/3?i-1:i+1);
addEventListener('keydown',(e)=>{{if(e.key==='ArrowRight'||e.key===' ')show(i+1);if(e.key==='ArrowLeft')show(i-1);}});
let x0=null;img.addEventListener('touchstart',(e)=>{{x0=e.touches[0].clientX}},{{passive:true}});
img.addEventListener('touchend',(e)=>{{if(x0===null)return;const d=e.changedTouches[0].clientX-x0;if(Math.abs(d)>40)show(d<0?i+1:i-1);x0=null;}});
show(1);
</script>
</body>
</html>
"""


def viewer_texts(title, viewer, sources):
    """ビューアの紹介文・シェア文・足元。sources は出典のURLの並び（記事から作ったときは記事の1件）。"""
    link = lambda u: f'<a href="{html.escape(u)}" rel="noopener nofollow" target="_blank">{html.escape(urllib.parse.urlparse(u).netloc)}</a>'
    site = f'<a href="{SITE}/">みのるんエージェント</a>'
    if KIND["value"] == "topic":
        made, verb = "調べてまとめた", "調べて"
        footer = (("出典：" + "、".join(link(u) for u in sources) + "<br>") if sources else "") + \
            f"{site}がナレッジベースとWebで調べて自動で作ったスライドです。内容は出典で確かめてください。"
    else:
        made, verb = "記事から作った", "記事を渡して"
        footer = f"元の記事：{link(sources[0])}<br>{site}がこの記事から自動で作ったスライドです。内容は元の記事で確かめてください。"
    share = "https://x.com/intent/post?text=" + urllib.parse.quote(
        f"#みのるんエージェント に{verb}「{title}」のスライドを作ってもらったよ！\n{viewer}")
    return {"made": made, "share": html.escape(share), "footer": footer}


SAVE = '<a class="save" href="deck.pptx" download title="1ページを1枚の画像にしたPowerPoint（文字は編集できません）">PPTXで保存</a>'


def pptx_name(title):
    """保存するときのファイル名。ファイル名に使えない字を外し、長すぎる題は切る"""
    return (re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", title).strip()[:60] or "スライド") + ".pptx"


def build_pptx(images, dst):
    """焼いた画像を1ページ1枚で全面に貼った PowerPoint を作る。文字は画像なので編集できない（見た目はビューアと同じ）"""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)  # 16:9（13.333×7.5インチ）
    blank = prs.slide_layouts[6]
    for img in images:
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(str(img), 0, 0, prs.slide_width, prs.slide_height)
    prs.save(dst)
    return dst


def upload_pptx(job, path, title):
    s3.upload_file(str(path), BUCKET, f"s/{job}/deck.pptx", ExtraArgs={
        "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "ContentDisposition": "attachment; filename=\"slides.pptx\"; filename*=UTF-8''" + urllib.parse.quote(pptx_name(title)),
        "CacheControl": "public, max-age=31536000, immutable"})


def publish(job, work, md, sources, pages):
    title = title_of(md)
    base = f"s/{job}/"
    files = sorted((work / "png").glob("*.png"))
    for p in files:
        im = Image.open(p).convert("RGB")
        dst = p.with_suffix(".webp")
        im.save(dst, "WEBP", quality=86, method=5)
        s3.upload_file(str(dst), BUCKET, base + dst.name,
                       ExtraArgs={"ContentType": "image/webp", "CacheControl": "public, max-age=31536000, immutable"})
    # X のカードと画面のサムネイル（表紙を 1200×675 に）
    cover = Image.open(files[0]).convert("RGB").resize((1200, 675), Image.LANCZOS)
    cover.save(work / "og.jpg", "JPEG", quality=86)
    s3.upload_file(str(work / "og.jpg"), BUCKET, base + "og.jpg",
                   ExtraArgs={"ContentType": "image/jpeg", "CacheControl": "public, max-age=31536000, immutable"})
    # PowerPoint には PNG のまま貼ると1本10MBを超えるので、文字がにじまない画質の JPEG にする
    jpgs = []
    for p in files:
        j = p.with_suffix(".jpg")
        Image.open(p).convert("RGB").save(j, "JPEG", quality=92, subsampling=0)
        jpgs.append(j)
    upload_pptx(job, build_pptx(jpgs, work / "deck.pptx"), title)
    viewer = f"{SITE}/{base}"
    page = VIEWER.format(title=html.escape(title), pages=pages, url=viewer, og=viewer + "og.jpg", site=SITE, save=SAVE,
                         **viewer_texts(title, viewer, sources))
    s3.put_object(Bucket=BUCKET, Key=base + "index.html", Body=page.encode(),
                  ContentType="text/html; charset=utf-8", CacheControl="public, max-age=300")
    s3.put_object(Bucket=BUCKET, Key=base + "deck.md", Body=md.encode(), ContentType="text/markdown; charset=utf-8")
    return title, viewer


async def read_sources(urls):
    """出典のページを並べて読む。読めなかったページは飛ばす（要点だけでも作れる）"""
    pages = await asyncio.gather(*[read_url(u) for u in urls], return_exceptions=True)
    out = []
    for u, page in zip(urls, pages):
        text = page.get("page_text", "") if isinstance(page, dict) and page.get("status") == "ok" else ""
        out.append({"url": u, "text": text[:SOURCE_CHARS]})
    return out


SOURCE_CHARS = 6000  # 出典1件あたり。5件で約3万字に収め、1本の費用を記事のときと同じくらいにする


def article_prompt(job, url, ask):
    """記事を読んで、モデルへ渡す依頼文を作る。読めなければ None"""
    status(job, "reading")
    page = asyncio.run(read_url(url))
    text = page.get("page_text", "")
    if page.get("status") != "ok" or len(text) < 400:
        page = read_with_browser(url) or page
        text = page.get("page_text", "")
    if page.get("status") != "ok" or len(text) < 400:
        return None, None
    return (f"次の記事を解説スライドにしてください。\nURL: {page.get('url') or url}\n"
            + (f"頼んだ人の一言: {ask}\n" if ask else "")
            + f"\n<article>\n{text}\n</article>"), urllib.parse.urlparse(page.get("url") or url).netloc


def topic_prompt(job, event, ask):
    """調べた要点と出典の本文から、モデルへ渡す依頼文を作る"""
    status(job, "reading")
    urls = [u for u in event.get("sources") or [] if isinstance(u, str) and u.startswith(("http://", "https://"))][:5]
    read = asyncio.run(read_sources(urls)) if urls else []
    body = "\n".join(f'<source n="{i}" url="{html.escape(r["url"])}">\n{r["text"]}\n</source>'
                      for i, r in enumerate(read, 1) if r["text"])
    print(json.dumps({"job": job, "sources": len(urls), "read": sum(1 for r in read if r["text"])}))
    return (f"次のテーマを、材料だけを使って解説スライドにしてください。\nテーマ: {event['topic']}\n"
            + (f"頼んだ人の一言: {ask}\n" if ask else "")
            + "出典のURL（番号の順）:\n" + "\n".join(f"［{i}］{u}" for i, u in enumerate(urls, 1)) + "\n"
            + f"\n<notes>\n{event['notes']}\n</notes>\n" + body)


def handler(event, context):
    job, ask = event["job"], str(event.get("ask", ""))[:300]
    KIND["value"] = "topic" if event.get("topic") else "article"
    started = time.time()
    work = Path("/tmp") / job
    usage = []
    try:
        if KIND["value"] == "topic":
            rules, sources = TOPIC_RULES, [u for u in event.get("sources") or [] if isinstance(u, str)][:5]
            prompt, domain = topic_prompt(job, event, ask), "agent.minoruonda.com"
        else:
            rules, sources = RULES, [event["url"]]
            prompt, domain = article_prompt(job, event["url"], ask)
            if prompt is None:
                status(job, "error", message="記事の本文を読めませんでした。ログインが要るページや、画像・動画だけのページは使えません。")
                return {"ok": False, "reason": "unreadable"}
        status(job, "writing")
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        raw, u = ask_model(messages, rules)
        usage.append(u)
        refs = sources if KIND["value"] == "topic" else ()
        md = deck(fix_cover(clean(raw)), domain, refs)
        status(job, "rendering")
        result = render(md, work)
        status(job, "checking")
        if result["ng"]:
            # 崩れたスライドだけを伝えて1回だけ直させる（何度も回すと時間と費用が膨らむ）
            status(job, "fixing")
            lines = "\n".join(f"- {r['n']}スライド目：{'、'.join(r['bad'])}" for r in result["ng"])
            messages += [{"role": "assistant", "content": [{"text": raw}]},
                         {"role": "user", "content": [{"text": "焼いて余白を測ったところ、次のスライドが崩れました。"
                          "該当スライドの文字を減らす・項目を減らす・2つのスライドに分けるなどで直し、全体をもう一度、同じ形式で出してください。"
                          "最後の締めのスライドと出典のスライドは、こちらで足したもの（直さなくてよい）。\n" + lines}]}]
            raw2, u = ask_model(messages, rules)
            usage.append(u)
            md2 = deck(fix_cover(clean(raw2)), domain, refs)
            result2 = render(md2, work)
            if len(result2["ng"]) <= len(result["ng"]):
                md, result = md2, result2
            else:
                render(md, work)
        md, result = shrink_to_fit(md, result, work)
        title, viewer = publish(job, work, md, sources, result["pages"])
        usd = sum(x["input"] * 4 + x["output"] * 20 for x in usage) / 1_000_000
        status(job, "done", title=title, pages=result["pages"], viewer=viewer, thumb=viewer + "og.jpg")
        print(json.dumps({"job": job, "ok": True, "kind": KIND["value"], "pages": result["pages"], "ng_left": len(result["ng"]),
                          "rounds": len(usage), "usage": usage, "usd": round(usd, 4),
                          "seconds": round(time.time() - started, 1)}))
        return {"ok": True}
    except Exception as e:
        print(json.dumps({"job": job, "ok": False, "error": type(e).__name__, "detail": str(e)[:500], "stderr": (getattr(e, "stderr", None) or b"")[-1500:].decode("utf-8", "replace") if isinstance(getattr(e, "stderr", None), bytes) else str(getattr(e, "stderr", "") or "")[-1500:],
                          "usage": usage, "seconds": round(time.time() - started, 1)}))
        status(job, "error", message="スライドを作れませんでした。少し時間をおいて、もう一度頼んでください。")
        return {"ok": False}
    finally:
        shutil.rmtree(work, ignore_errors=True)
