"""README のアーキテクチャ図（docs/images/architecture.svg / .png）を書き出す。

アイコンは AWS 公式（Architecture Icons 2026-01-30 版）を docs/images/icons に写したもの。
使い方: python3 docs/images/architecture.py   （PNG への変換に rsvg-convert を使う）
"""
import base64
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
W, H = 1500, 760
FONT = "Hiragino Sans, Hiragino Kaku Gothic ProN, Noto Sans JP, sans-serif"


def icon(name):
    p = ICONS / name
    mime = "image/svg+xml" if p.suffix == ".svg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


CF, S3, AC, LAM, DDB, BR = ("Arch_Amazon-CloudFront_64.svg", "Arch_Amazon-Simple-Storage-Service_64.svg",
                            "Arch_Amazon-Bedrock-AgentCore_64.svg", "Arch_AWS-Lambda_64.svg",
                            "Arch_Amazon-DynamoDB_64.svg", "Arch_Amazon-Bedrock_64.svg")
PHONE, NET = "Res_Mobile-client_48_Light.svg", "Res_Internet_48_Light.svg"

# id: (中心x, 中心y, アイコン, 名前, 補足)
NODES = {
    "phone": (90, 250, PHONE, "ブラウザ", "スマホ"),
    "cf": (290, 250, CF, "CloudFront", "画面と /api の入口"),
    "web": (290, 490, S3, "S3", "画面（Vite）"),
    "gw": (520, 250, AC, "AgentCore Gateway", "入口・HTTP ターゲット"),
    "lam": (520, 490, LAM, "Lambda", "インターセプター（回数・字数）"),
    "use": (520, 660, DDB, "DynamoDB", "利用回数"),
    "br": (780, 80, BR, "Amazon Bedrock", "Sonnet 5.5・Haiku 5.5"),
    "rt": (780, 250, AC, "AgentCore Runtime", "Strands Agents（Python）"),
    "tgw": (1030, 250, AC, "AgentCore Gateway", "ツール（MCP）"),
    "kb": (1240, 140, BR, "Managed Knowledge Base", "2026年と過去を分けて検索"),
    "ws": (1240, 320, AC, "AgentCore Web Search", "Web 検索"),
    "mem": (700, 490, AC, "AgentCore Memory", "会話の文脈（7日）"),
    "log": (880, 490, DDB, "DynamoDB", "会話の記録"),
    "ses": (1060, 490, S3, "S3", "セッションの控え"),
    "ref": (1220, 660, LAM, "Lambda", "15分おきに取り直す"),
    "api": (1435, 490, NET, "公式セッション", "カタログ API"),
}
R = 32  # アイコンの半径


def node(cx, cy, ic, name, sub):
    return (f'<image href="{icon(ic)}" x="{cx - R}" y="{cy - R}" width="{2 * R}" height="{2 * R}"/>'
            f'<text x="{cx}" y="{cy + R + 22}" class="n">{name}</text>'
            f'<text x="{cx}" y="{cy + R + 41}" class="s">{sub}</text>')


def line(points, label=None, at=None, dashed=False, arrow=True):
    d = " ".join(f"{x},{y}" for x, y in points)
    s = f'<polyline points="{d}" class="e{" d" if dashed else ""}"{" marker-end=\"url(#a)\"" if arrow else ""}/>'
    if label:
        x, y = at
        s += f'<text x="{x}" y="{y}" class="l">{label}</text>'
    return s


def main():
    N = NODES
    x = lambda k: N[k][0]
    y = lambda k: N[k][1]
    g = R + 8   # 線の端をアイコンから離す
    lb = R + 52  # ラベルの下端
    edges = [
        line([(x("phone") + g, 250), (x("cf") - g, 250)]),
        line([(x("cf"), y("cf") + lb), (x("web"), y("web") - g)], "画面", (x("cf") + 26, 400)),
        line([(x("cf") + g, 250), (x("gw") - g, 250)], "/api/chat", (405, 238)),
        line([(x("gw"), y("gw") + lb), (x("lam"), y("lam") - g)], "通してよいか", (x("gw") + 52, 400)),
        line([(x("lam"), y("lam") + lb), (x("use"), y("use") - g)]),
        line([(x("gw") + g, 250), (x("rt") - g, 250)], "SSE", (650, 238)),
        line([(x("rt"), y("rt") - g), (x("br"), y("br") + lb)]),
        line([(x("rt") + g, 250), (x("tgw") - g, 250)], "MCP", (905, 238)),
        line([(x("tgw") + g, 240), (1120, 240), (1120, 140), (x("kb") - g, 140)]),
        line([(x("tgw") + g, 260), (1120, 260), (1120, 320), (x("ws") - g, 320)]),
        # Runtime の下から1本の線で会話の保存先とセッションの控えへ。続きは公式 API での照合
        line([(x("rt"), y("rt") + lb), (x("rt"), 410)], arrow=False),
        line([(x("mem"), 410), (x("ses"), 410)], arrow=False),
        line([(x("mem"), 410), (x("mem"), y("mem") - g)]),
        line([(x("log"), 410), (x("log"), y("log") - g)]),
        line([(x("ses"), 410), (x("ses"), y("ses") - g)]),
        line([(x("ses"), 410), (x("api"), 410), (x("api"), y("api") - g)], "回答後に照合", (1250, 434), dashed=True),
        line([(x("ref") - 24, y("ref") - g), (x("ses") + 20, y("ses") + lb + 4)]),
        line([(x("ref") + 24, y("ref") - g), (x("api") - 20, y("api") + lb + 4)]),
    ]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0,0 L10,5 L0,10 z" fill="#6b7280"/></marker></defs>
<style>
text{{font-family:{FONT};text-anchor:middle}}
.n{{font-size:15px;font-weight:700;fill:#16181d}}
.s{{font-size:12.5px;fill:#6b7280}}
.l{{font-size:12.5px;fill:#374151;paint-order:stroke;stroke:#fff;stroke-width:5px}}
.e{{fill:none;stroke:#6b7280;stroke-width:1.8}}
.d{{stroke-dasharray:6 5}}
.b{{fill:none;stroke:#232f3e;stroke-width:1.4;stroke-dasharray:2 0}}
.bt{{font-size:13px;font-weight:700;fill:#232f3e;text-anchor:start}}
</style>
<rect width="{W}" height="{H}" fill="#fff"/>
<rect x="190" y="20" width="1172" height="720" rx="14" class="b"/>
<text x="206" y="44" class="bt">AWS（us-west-2）</text>
{"".join(edges)}
{"".join(node(*v) for v in N.values())}
</svg>'''
    (HERE / "architecture.svg").write_text(svg)
    subprocess.run(["rsvg-convert", "-z", "2", "-o", str(HERE / "architecture.png"), str(HERE / "architecture.svg")],
                   check=True)


if __name__ == "__main__":
    main()
