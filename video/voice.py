"""字幕1行ずつを、VOICEVOX ENGINE（同じ入れ物の中で動かす）でずんだもんの声にする。

ENGINE が動かないときに別の合成音声へ黙って切り替えない（みのるんの決まり）。失敗として止める。
"""

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

ENGINE = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")
SPEAKER_NAME, STYLE_NAME = "ずんだもん", "ノーマル"
CREDIT = "VOICEVOX:ずんだもん"
TARGET_SECONDS = 180   # 3分
MAX_SECONDS = 190      # これを超えたら速さを上げて作り直す
# 標準は1.25倍（2026-11-26 みのるん指定。ふだんの解説動画と同じ速さ）。長すぎたときだけ上げ、上限は1.5倍
BASE_SPEED = 1.25
MAX_SPEED = 1.5


def _get(path, timeout=10):
    with urllib.request.urlopen(ENGINE + path, timeout=timeout) as r:
        return r.read()


def _post(path, body=None, timeout=120):
    req = urllib.request.Request(ENGINE + path, data=body or b"", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def start_engine(wait=180):
    """入れ物の中の ENGINE を起こし、答えるまで待つ。手元で別に動いていればそれを使う"""
    try:
        _get("/version", timeout=2)
        return
    except OSError:
        pass
    run = os.environ.get("VOICEVOX_RUN", "/opt/voicevox_engine/run")
    if Path(run).exists():
        subprocess.Popen([run, "--host", "127.0.0.1", "--port", "50021", "--cpu_num_threads", str(os.cpu_count() or 2)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + wait
    while time.time() < end:
        try:
            _get("/version", timeout=2)
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError("VOICEVOX ENGINE が起動しませんでした")


def stop_engine():
    """声を録り終えたら ENGINE を止め、描画の Chromium へ CPU とメモリを空ける（動いたままだと Chromium の起動が時間切れになった）"""
    import signal
    for d in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if b"voicevox_engine" in cmd and int(d.name) != os.getpid():
            try:
                os.kill(int(d.name), signal.SIGTERM)
            except OSError:
                pass


def speaker_id():
    for s in json.loads(_get("/speakers")):
        if s["name"] == SPEAKER_NAME:
            for st in s["styles"]:
                if st["name"] == STYLE_NAME:
                    return st["id"]
    raise RuntimeError("ずんだもん ノーマルが見つかりません")


def _seconds(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def synth(lines, out_dir, speed=BASE_SPEED):
    """lines は読ませる文の並び。1行1つの WAV を書き、[(パス, 秒)] を返す"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sid = speaker_id()
    out = []
    for i, text in enumerate(lines):
        q = json.loads(_post(f"/audio_query?{urllib.parse.urlencode({'text': text, 'speaker': sid})}"))
        q.update(speedScale=speed, prePhonemeLength=0.08, postPhonemeLength=0.25, intonationScale=1.1)
        wav = _post(f"/synthesis?speaker={sid}", json.dumps(q).encode(), timeout=300)
        path = out_dir / f"l{i:03d}.wav"
        path.write_bytes(wav)
        out.append((path, _seconds(path)))
    return out


def voice_story(story, out_dir):
    """台本の全行を声にする。長すぎたら速さを上げて1回だけ作り直す。各行に audio・sec を書き足して合計秒を返す"""
    rows = [ln for sc in story["scenes"] for ln in sc["lines"]]
    speed = BASE_SPEED
    got = synth([r["say"] for r in rows], out_dir, speed)
    total = sum(s for _, s in got)
    if total > MAX_SECONDS:
        speed = min(MAX_SPEED, round(speed * total / TARGET_SECONDS, 2))
        got = synth([r["say"] for r in rows], out_dir, speed)
        total = sum(s for _, s in got)
    for r, (p, s) in zip(rows, got):
        r["audio"], r["sec"] = str(p), round(s, 3)
    story["speed"] = speed
    return total
