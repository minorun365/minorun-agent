"""動画作成機能の入口（AgentCore Runtime。2026-11-26）。作りはスライド作成機能の slides/app.py と同じ。

ジョブを受け取ったらすぐ「受け付けた」と返し、作るのは裏のスレッドで行う。作っている間は非同期タスクとして登録し、
ping が HealthyBusy を返すのでセッションを畳まれない。1セッション＝1つの入れ物＝1本。
VOICEVOX ENGINE は受け付けた時点で起こし始め、資料を読んでいる間に立ち上がりを済ませる。
"""

import json
import threading

from bedrock_agentcore.runtime import BedrockAgentCoreApp

import handler
import voice

app = BedrockAgentCoreApp()
RUNNING = {}


def _work(event, task_id):
    try:
        handler.handler(event, None)
    except Exception as e:
        print(json.dumps({"job": event.get("job"), "ok": False, "error": type(e).__name__, "detail": str(e)[:500]}))
    finally:
        RUNNING.pop(event.get("job"), None)
        app.complete_async_task(task_id)


def _warm():
    try:
        voice.start_engine()
    except Exception as e:  # 本番の流れの中でもう一度起こすので、ここでは記録だけ
        print(json.dumps({"voicevox_warm_error": type(e).__name__}))


@app.entrypoint
def invoke(payload, context=None):
    job = payload.get("job") if isinstance(payload, dict) else None
    if not job or not payload.get("doc"):
        return {"accepted": False, "reason": "job と doc が要ります"}
    if job in RUNNING:
        return {"accepted": True, "job": job, "duplicate": True}
    task_id = app.add_async_task("video", {"job": job})
    RUNNING[job] = task_id
    threading.Thread(target=_warm, daemon=True).start()
    threading.Thread(target=_work, args=(payload, task_id), daemon=True).start()
    return {"accepted": True, "job": job}


if __name__ == "__main__":
    app.run()
