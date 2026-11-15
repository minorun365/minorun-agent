"""スライド工房の入口（AgentCore Runtime。2026-11-16 に Lambda から移した）。

エージェントのツール make_slides が、ジョブごとに別のセッション ID で invoke_agent_runtime を呼ぶ。
ここはジョブを受け取ったらすぐ「受け付けた」と返し、作るのは裏のスレッドで行う（呼び出し側を待たせない）。
作っている間は非同期タスクとして登録し、ping が HealthyBusy を返すので、途中でセッションを畳まれない。
終われば Idle に戻り、Runtime の idle の設定（60秒）でセッションが閉じる。1セッション＝1つの入れ物＝1本なので、
ジョブどうしが同じ /tmp やモジュールの状態を取り合わない。
"""

import json
import threading

from bedrock_agentcore.runtime import BedrockAgentCoreApp

import handler

app = BedrockAgentCoreApp()
RUNNING = {}


def _work(event, task_id):
    try:
        handler.handler(event, None)
    except Exception as e:  # handler の中で捕まえきれなかったもの。status は handler が書けていない
        print(json.dumps({"job": event.get("job"), "ok": False, "error": type(e).__name__, "detail": str(e)[:500]}))
    finally:
        RUNNING.pop(event.get("job"), None)
        app.complete_async_task(task_id)


@app.entrypoint
def invoke(payload, context=None):
    job = payload.get("job") if isinstance(payload, dict) else None
    if not job:
        return {"accepted": False, "reason": "job がありません"}
    if job in RUNNING:  # 同じジョブの二重起動（呼び出しの再試行）は無視する
        return {"accepted": True, "job": job, "duplicate": True}
    task_id = app.add_async_task("slides", {"job": job})
    RUNNING[job] = task_id
    threading.Thread(target=_work, args=(payload, task_id), daemon=True).start()
    return {"accepted": True, "job": job}


if __name__ == "__main__":
    app.run()
