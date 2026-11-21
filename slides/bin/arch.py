"""Harness のエージェントが使う：アーキテクチャ図を、決まった体裁で描く（2026-11-21）。

diagrams の既定の描き方は、ラベルがアイコンの下端に重なり、矢印がラベルを横切り、長いラベルが切れる（通しテストで見つけた）。
そこで、アイコンとラベルを1つの表（Graphviz の HTML ラベル）にまとめ、矢印は表の外枠につなぐ。アイコンは diagrams に入っている公式のものを使う。

使い方:
  python3 /app/bin/arch.py <図の JSON> <出力 PNG>      図を描く
  python3 /app/bin/arch.py --icons <語>                 アイコンを探す（例：lambda、gateway、agentcore、user）。AgentCore は local/agentcore

図の JSON:
{"direction": "LR",                                     # LR（横）か TB（縦）。スライドは横長なので LR が基本
 "nodes": [{"id": "u", "icon": "aws/general/user", "label": "利用者"},
           {"id": "cf", "icon": "aws/network/cloudfront", "label": "CloudFront"}],
 "edges": [["u", "cf"], ["cf", "s3", {"label": "画面", "style": "dashed"}]],
 "groups": [{"label": "AWS", "nodes": ["cf", "s3"]}]}   # 囲みは要るときだけ
"""
import json
import subprocess
import sys
from html import escape
from pathlib import Path

import diagrams

RES = Path(diagrams.__file__).resolve().parent.parent / "resources"
# diagrams に無い公式アイコン（AWS Architecture Icons。図や資料に使うことを AWS が認めている）。"local/<名前>" で指定する
LOCAL = Path(__file__).resolve().parent / "icons"
FONT = "Noto Sans JP"
INK, LINE, MUTE = "#24343e", "#0891b2", "#647b88"
MAX_NODES = 8


def icons(word):
    word = word.lower()
    hits = sorted(f"local/{p.stem}" for p in LOCAL.glob("*.png") if word in p.stem.lower())
    hits += sorted(str(p.relative_to(RES).with_suffix("")) for p in RES.rglob("*.png") if word in p.stem.lower())
    print("\n".join(hits[:60]) or "見つかりません。別の語で探してください")


def wrap(label, width=11):
    """長いラベルを語の切れ目で2行までに折る（全角は2字、半角は1字で数える）"""
    words, lines, cur = label.split(" "), [], ""
    size = lambda s: sum(2 if ord(c) > 0x2E7F else 1 for c in s)
    for w in words:
        if cur and size(cur + " " + w) > width * 2 * 0.9:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return "<BR/>".join(escape(x) for x in lines[:2])


def node(n):
    icon = LOCAL / f"{n['icon'][6:]}.png" if n["icon"].startswith("local/") else RES / f"{n['icon']}.png"
    if not icon.exists():
        sys.exit(f"アイコンが見つかりません：{n['icon']}（--icons で探す）")
    return (f'"{n["id"]}" [shape=plain label=<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6">'
            f'<TR><TD FIXEDSIZE="TRUE" WIDTH="84" HEIGHT="84"><IMG SRC="{icon}" SCALE="TRUE"/></TD></TR>'
            f'<TR><TD><FONT POINT-SIZE="19" COLOR="{INK}">{wrap(n["label"])}</FONT></TD></TR></TABLE>>];')


def edge(e):
    a, b = e[0], e[1]
    opt = e[2] if len(e) > 2 and isinstance(e[2], dict) else {}
    attrs = []
    if opt.get("style") == "dashed":
        attrs.append("style=dashed")
    if opt.get("label"):
        attrs.append(f'label=< <FONT POINT-SIZE="16" COLOR="{MUTE}">{escape(opt["label"])}</FONT> >')
    if opt.get("both"):
        attrs.append("dir=both")
    return f'"{a}" -> "{b}" [{" ".join(attrs)}];'


def draw(spec, out):
    nodes = spec["nodes"]
    if len(nodes) > MAX_NODES:
        sys.exit(f"アイコンが {len(nodes)} 個あります。{MAX_NODES} 個までにして、多いなら図を2枚に分ける")
    ids = {n["id"] for n in nodes}
    for e in spec.get("edges", []):
        if e[0] not in ids or e[1] not in ids:
            sys.exit(f"矢印の端 {e[0]} → {e[1]} が nodes にありません")
    grouped = set()
    lines = [f'digraph G {{ rankdir={spec.get("direction", "LR")}; bgcolor="transparent"; pad=0.15; dpi=150;',
             f'nodesep=0.55; ranksep=0.95; fontname="{FONT}"; newrank=true;',
             f'node [fontname="{FONT}"]; edge [fontname="{FONT}" color="{LINE}" penwidth=2.6 arrowsize=1.1];']
    for i, g in enumerate(spec.get("groups", [])):
        lines.append(f'subgraph cluster_{i} {{ label=< <FONT POINT-SIZE="17" COLOR="{MUTE}">{escape(g["label"])}</FONT> >; '
                     f'labeljust=l; style="rounded,dashed"; color="#9cc9d6"; penwidth=1.6; margin=18;')
        for n in nodes:
            if n["id"] in g["nodes"]:
                lines.append(node(n))
                grouped.add(n["id"])
        lines.append("}")
    lines += [node(n) for n in nodes if n["id"] not in grouped]
    lines += [edge(e) for e in spec.get("edges", [])]
    lines.append("}")
    subprocess.run(["dot", "-Tpng", "-o", out], input="\n".join(lines).encode(), check=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--icons":
        icons(sys.argv[2])
    elif len(sys.argv) == 3:
        draw(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2])
    else:
        sys.exit(__doc__)
