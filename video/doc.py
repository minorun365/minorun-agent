"""資料（PDF・PowerPoint）を、ページごとの画像と文字に分ける。

PowerPoint は LibreOffice で一度 PDF にしてから同じ道を通す（見た目どおりの画像を得るため）。
スライドのノート（発表者メモ）は画像に出ないので、python-pptx で別に読んでページの文字へ足す。
"""

import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

MAX_PAGES = 60          # これより多い資料は先頭から60ページだけ使う
MAX_BYTES = 40_000_000  # 受け付ける資料の大きさ（画面の上限と同じ）
PAGE_PX = 1600          # 画面に大きく映す用の幅
LOOK_PX = 1000          # モデルに見せる用の幅（図の読み取りに足りて、トークンを食いすぎない）


class DocError(Exception):
    """利用者に見せてよい理由つきの失敗"""


def kind_of(path):
    head = Path(path).read_bytes()[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                total = sum(i.file_size for i in z.infolist())
        except zipfile.BadZipFile:
            return None
        # 展開すると何百倍にも膨らむ細工（zip 爆弾）を開く前に止める
        if total > 400_000_000:
            raise DocError("資料を開けませんでした（中身が大きすぎます）。")
        if any(n.startswith("ppt/slides/") for n in names):
            return "pptx"
    return None


def _run(cmd, timeout):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-400:]}")
    return r.stdout


def soffice():
    for name in ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        p = shutil.which(name) or (name if Path(name).exists() else None)
        if p:
            return p
    raise RuntimeError("LibreOffice が見つかりません")


def pptx_to_pdf(src, work):
    # 利用者の設定置き場を毎回まっさらにする（前の資料の名残や同時起動の取り合いを避ける）
    profile = Path(tempfile.mkdtemp(dir=work, prefix="lo-"))
    _run([soffice(), f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
          "--convert-to", "pdf", "--outdir", str(work), str(src)], timeout=300)
    out = Path(work) / (Path(src).stem + ".pdf")
    if not out.exists():
        raise DocError("PowerPoint を開けませんでした。PDF に書き出してから送ってみてください。")
    return out


def pptx_notes(src):
    """ページ番号（1始まり）→ ノートの文字。読めなければ空"""
    try:
        from pptx import Presentation
        prs = Presentation(str(src))
    except Exception:
        return {}
    notes = {}
    for i, slide in enumerate(prs.slides, 1):
        if slide.has_notes_slide:
            t = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if t:
                notes[i] = t[:1500]
    return notes


def page_count(pdf):
    for line in _run(["pdfinfo", str(pdf)], timeout=60).splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 0


def split(src, work):
    """資料を開いて pages.json と画像を work に書く。戻り値はページの一覧。
    各ページ：{"n": 番号, "image": 大きい画像, "look": モデル用の画像, "text": 文字, "notes": ノート}"""
    src, work = Path(src), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if src.stat().st_size > MAX_BYTES:
        raise DocError("資料が大きすぎます（40MB まで）。")
    kind = kind_of(src)
    if kind is None:
        raise DocError("PDF か PowerPoint（.pptx）の資料を送ってください。")
    notes = {}
    pdf = src
    if kind == "pptx":
        notes = pptx_notes(src)
        pdf = pptx_to_pdf(src, work)
    try:
        total = page_count(pdf)
    except RuntimeError:
        raise DocError("PDF を開けませんでした。パスワード付きの資料は外してから送ってください。")
    if total == 0:
        raise DocError("資料にページがありませんでした。")
    last = min(total, MAX_PAGES)
    big, look = work / "pages", work / "look"
    big.mkdir(exist_ok=True)
    look.mkdir(exist_ok=True)
    _run(["pdftoppm", "-f", "1", "-l", str(last), "-jpeg", "-jpegopt", "quality=88",
          "-scale-to", str(PAGE_PX), str(pdf), str(big / "p")], timeout=600)
    _run(["pdftoppm", "-f", "1", "-l", str(last), "-jpeg", "-jpegopt", "quality=80",
          "-scale-to", str(LOOK_PX), str(pdf), str(look / "p")], timeout=600)
    pages = []
    bigs, looks = sorted(big.glob("p-*.jpg")), sorted(look.glob("p-*.jpg"))
    for i, (b, l) in enumerate(zip(bigs, looks), 1):
        text = _run(["pdftotext", "-f", str(i), "-l", str(i), "-layout", "-enc", "UTF-8", str(pdf), "-"], timeout=60)
        pages.append({"n": i, "image": str(b), "look": str(l), "text": " ".join(text.split())[:2500],
                      "notes": notes.get(i, "")})
    (work / "pages.json").write_text(json.dumps({"kind": kind, "total": total, "pages": pages}, ensure_ascii=False, indent=1))
    return {"kind": kind, "total": total, "pages": pages}


if __name__ == "__main__":
    import sys
    r = split(sys.argv[1], sys.argv[2])
    print(r["kind"], r["total"], len(r["pages"]))
