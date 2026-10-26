"""sources/extracted/*.json を検査し、機械で直せるずれは直す。
- 割り当て表にあるのに抽出ファイルが無いID（candidates で status: unreadable のものは除く）
- JSON が読めない／必須項目が無い／topic が topics.json に無い／事実が120字を超える
- URL が candidates.jsonl と食い違う
- 開催年（event_year）が公開年より後 → 公開年へ直す（re:Invent は年末開催なので、記事は必ず開催年以降に出る）
- 事実ごとの年（facts[].year）を付ける。既定は記事の開催年で、tools/year_overrides.json に載せた事実
  （去年の体験談など、記事と別の年の話）だけその年にする。新しい記事を足したら、記事の年と違う年に
  触れる事実を見て、必要なら year_overrides.json へ足してから --fix を走らせる
使い方: python3 tools/check_extracted.py [--fix]
"""
import glob, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
fix = "--fix" in sys.argv
topics = json.loads((ROOT / "topics.json").read_text())
cand = {json.loads(l)["id"]: json.loads(l) for l in (ROOT / "sources/candidates.jsonl").read_text().splitlines() if l.strip()}
assigned = []
for b in sorted(glob.glob(str(ROOT / "sources/batches/b*.txt"))):
    assigned += [(Path(b).stem, i) for i in Path(b).read_text().split()]
official = [("official", Path(p).stem) for p in sorted(glob.glob(str(ROOT / "sources/official/*.json")))]
official += [("profile", Path(p).stem) for p in sorted(glob.glob(str(ROOT / "sources/profile/*.json")))]
official += [(k, Path(p).stem) for k in ("own", "kag") for p in sorted(glob.glob(str(ROOT / f"sources/{k}/*.json")))
             if not p.endswith(("index.jsonl", "youtube.json"))]
ov = {k: v for k, v in json.loads((ROOT / "tools/year_overrides.json").read_text()).items() if not k.startswith("_")}
ng, fixed, scanned, nfacts = [], 0, 0, 0
yeared = 0
for batch, i in assigned + official:
    p = ROOT / "sources" / (batch if batch in ("official", "profile", "own", "kag") else "extracted") / f"{i}.json"
    if not p.exists():
        if cand.get(i, {}).get("status") == "unreadable":  # 本文が読めないと記録済み
            continue
        ng.append(f"{batch} {i} 未抽出"); continue
    scanned += 1
    try:
        d = json.loads(p.read_text())
    except Exception as e:
        ng.append(f"{i} JSON不正 {e}"); continue
    for k in ("url", "title", "event_year", "usefulness", "summary", "facts"):
        if k not in d: ng.append(f"{i} 項目なし {k}")
    if i in cand and d.get("url") != cand[i]["url"]: ng.append(f"{i} URL不一致")
    if batch == "official" and d.get("source_type") != "official": ng.append(f"{i} source_type が official でない")
    for f in d.get("facts", []):
        nfacts += 1
        if f.get("topic") not in topics: ng.append(f"{i} topic不正 {f.get('topic')}")
        if len(f.get("text", "")) > 120: ng.append(f"{i} 120字超 {len(f['text'])}")
    # 「開催年は公開年より後にならない」は参加者の記事だけの規則。公式・プロフィールは前年に公開された2026年の案内がある
    py = (d.get("published") or "")[:4] if batch not in ("official", "profile", "own", "kag") else ""
    if py.isdigit() and d.get("event_year", 0) > int(py) and fix:
        d["event_year"] = int(py)
    changed = False
    for f in d.get("facts", []):
        if batch in ("official", "profile", "own", "kag"):  # 公式・プロフィール・本人・KAG は読み取り時に事実ごとの年を付けている
            if not f.get("year"): ng.append(f"{i} 公式情報の事実に year が無い")
            continue
        y = next((v for k, v in ov.items() if f.get("text", "").startswith(k)), d.get("event_year", 0))
        if f.get("year") != y:
            f["year"] = y; changed = True
    if changed:
        if fix:
            p.write_text(json.dumps(d, ensure_ascii=False, indent=1)); yeared += 1
        else:
            ng.append(f"{i} 事実の年が未設定・古い（--fix で付く）")
    if py.isdigit() and d.get("event_year", 0) > int(py):
        if fix:
            d["event_year"] = int(py); p.write_text(json.dumps(d, ensure_ascii=False, indent=1)); fixed += 1
        else:
            ng.append(f"{i} 開催年{d['event_year']}>公開年{py}")
print(f"公式{len(official)}件と、割り当て{len(assigned)}件中 {scanned}件を走査、事実{nfacts}件、開催年の修正{fixed}件、事実の年を付けた記事{yeared}件")
print("\n".join(ng) if ng else "（問題なし）")
