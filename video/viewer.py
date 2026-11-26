"""できた動画を開くページ（v/<ジョブ>/index.html）。見た目はスライドのビューアにそろえる。

parts で動画の下に何を置くかを切り替える（qa：この動画で答える3つの問い、captions：字幕の全文）。
"""

import html
import urllib.parse

SITE = "https://agent.minoruonda.com"

PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>{title}｜みのるんエージェント</title>
<meta property="og:type" content="video.other">
<meta property="og:title" content="{title}">
<meta property="og:description" content="みのるんエージェントが資料から作った{minutes}の解説動画">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{url}poster.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{url}poster.jpg">
<style>
:root{{--bg:#eef7fa;--ink:#16323f;--mute:#5b707c;--btn:#ffffff;--line:#d6e6ed;--accent:#0891b2;--card:#ffffff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d1b24;--ink:#e6f1f5;--mute:#9db0bb;--btn:#16303d;--line:#24414f;--card:#132a36}}}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;background:var(--bg);color:var(--ink);font-family:"Hiragino Sans","Noto Sans JP",sans-serif;display:flex;flex-direction:column;align-items:center}}
header{{width:100%;max-width:1100px;padding:18px 16px 10px;display:flex;align-items:center;gap:12px}}
header h1{{flex:1;margin:0;font-size:17px;line-height:1.4;font-weight:700}}
.share,.save{{flex:none;display:inline-flex;align-items:center;gap:6px;background:#0f1419;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:9px 16px;border-radius:999px;white-space:nowrap}}
.save{{background:var(--btn);color:var(--ink);border:1px solid var(--line)}}
@media (max-width:520px){{header{{flex-wrap:wrap}}header h1{{flex-basis:100%}}}}
.stage{{width:100%;max-width:1100px;padding:0 16px}}
.stage video{{display:block;width:100%;height:auto;aspect-ratio:16/9;border-radius:12px;box-shadow:0 14px 40px rgba(8,100,130,.18);background:#0d1b24}}
main{{width:100%;max-width:1100px;padding:18px 16px 0}}
main h2{{font-size:15px;margin:6px 0 12px;color:var(--mute);font-weight:700}}
.qa{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
@media (max-width:760px){{.qa{{grid-template-columns:1fr}}}}
.qa li{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.qa b{{display:block;font-size:15px;line-height:1.5}}
.qa span{{display:block;margin-top:6px;font-size:14px;line-height:1.6;color:var(--mute)}}
.caps{{list-style:none;margin:0;padding:0;background:var(--card);border:1px solid var(--line);border-radius:12px;max-height:340px;overflow:auto}}
.caps li{{display:flex;gap:12px;padding:9px 14px;border-top:1px solid var(--line);font-size:14px;line-height:1.6;cursor:pointer}}
.caps li:first-child{{border-top:0}}
.caps li.on{{background:rgba(8,145,178,.1)}}
.caps time{{flex:none;color:var(--accent);font-variant-numeric:tabular-nums}}
footer{{padding:20px 16px 28px;font-size:13px;color:var(--mute);text-align:center;line-height:1.7}}
footer a{{color:var(--accent)}}
</style>
</head>
<body>
<header><h1>{title}</h1><a class="save" href="video.mp4" download>MP4で保存</a><a class="share" href="{share}" target="_blank" rel="noopener">Xでシェア</a></header>
<div class="stage"><video id="v" src="video.mp4" poster="poster.jpg" controls playsinline preload="metadata"></video></div>
{main}
<footer>{footer}</footer>
{script}
</body>
</html>
"""

CAPS_SCRIPT = """<script>
const v=document.getElementById('v'),items=[...document.querySelectorAll('.caps li')];
items.forEach(li=>li.onclick=()=>{v.currentTime=+li.dataset.t;v.play();});
v.addEventListener('timeupdate',()=>{let k=-1;items.forEach((li,i)=>{if(+li.dataset.t<=v.currentTime+.05)k=i;});
items.forEach((li,i)=>li.classList.toggle('on',i===k));});
</script>"""


def _mmss(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def render(meta, url, parts=("qa",)):
    """meta は handler が書く meta.json と同じ形（title・questions・answers・seconds・captions・name）"""
    esc = html.escape
    title = esc(meta["title"])
    blocks = []
    if "qa" in parts and meta.get("questions"):
        rows = "".join(f"<li><b>{esc(q)}</b><span>{esc(a)}</span></li>"
                       for q, a in zip(meta["questions"], meta.get("answers") or []))
        blocks.append(f"<h2>この動画でわかること</h2><ol class=\"qa\">{rows}</ol>")
    if "captions" in parts and meta.get("captions"):
        t, rows = 0.0, []
        for c in meta["captions"]:
            rows.append(f"<li data-t=\"{t:.2f}\"><time>{_mmss(t)}</time><span>{esc(c['text'])}</span></li>")
            t += float(c.get("sec") or 0)
        blocks.append(f"<h2>字幕</h2><ol class=\"caps\">{''.join(rows)}</ol>")
    name = esc(meta.get("name") or "")
    footer = ((f"元の資料：{name}<br>" if name else "")
              + f'<a href="{SITE}/">みのるんエージェント</a>が資料から自動で作った解説動画です。内容は元の資料で確かめてください。<br>'
              + "音声：VOICEVOX:ずんだもん")
    share = "https://x.com/intent/post?text=" + urllib.parse.quote(
        f"#みのるんエージェント に資料を渡して「{meta['title']}」の解説動画を作ってもらったよ！\n{url}")
    secs = int(meta.get("seconds") or 180)
    minutes = f"{max(1, round(secs / 60))}分"
    return PAGE.format(title=title, url=esc(url), share=esc(share), minutes=minutes,
                       main=f"<main>{''.join(blocks)}</main>" if blocks else "", footer=footer,
                       script=CAPS_SCRIPT if "captions" in parts else "")
