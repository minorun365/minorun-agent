"""みのるんエージェントの Runtime（Strands Agents）。

返事は SSE で次の形を順に流す（画面はこれだけを読む）:
  {"type": "topic", "heading": "...", "changed": true}   話題が変わったとき（区切り線と見出し）
  {"type": "tool", "name": "...", "label": "..."}         ツールを使い始めたとき（「調べています」の表示）
  {"type": "text", "text": "..."}                        本文の続き
  {"type": "follow_up", "text": "..."}                   最初の回答を残したまま追加調査を案内
  {"type": "replace", "text": "..."}                     追加調査後の要約で吹き出しの本文全体を置き換える
  {"type": "map", "src": "...", "alt": "...", ...}        回答に添える地図
  {"type": "preview", "url": "...", "image": "...", "title": "...", "label": "..."}  紹介した本人のページの貼り紙
  {"type": "catalog_update", "at": "HH:MM", "lines": [...]}  公式カタログと照合して変わっていた分（変更が無ければ送らない）
  {"type": "suggest", "items": ["…？", ...]}             次に聞きそうな質問（深掘り2つ＋別の話題1つ。作れなければ送らない）
  {"type": "error", "message": "..."}
  {"type": "done"}
返し終えた後、話題の整理・会話の控えの保存・1問1行の記録を裏で行う。
"""

import time

_BOOT = time.time()

import asyncio  # noqa: E402
import json
import threading

from bedrock_agentcore.runtime import BedrockAgentCoreApp, BedrockAgentCoreContext
from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry
from strands.models.bedrock import CacheConfig

import catalog
import link_preview
import prebaked
import prompt
import research
import store
import tls_probe  # noqa: F401  基盤の TLS の握手で警告が出ないようにする
import tools
import topics
import usage
from tool_status import knowledge_label
from settings import MAIN_EFFORT, MAX_TOOL_CALLS, MODEL_AUX, MODEL_MAIN

app = BedrockAgentCoreApp()

LABELS = {
    "now": "日時を確かめています",
    "search_sessions": "公式カタログを調べています",
    "search_2026": "2026年の情報を調べています",
    "search_past": "過去の参加記を調べています",
    "search_web": "Web を調べています",
    "search_recent": "最新のニュースを調べています",
    "get_announcements": "現地からの速報を見ています",
    "search_whats_new": "AWS の新着情報を調べています",
    "search_aws_blog": "AWS ブログを調べています",
    "show_map": "画像を用意しています",
}
SORRY = "いまうまく答えられませんでした。少し時間をおいて、もう一度送ってください。"
VERIFY_WAIT = 6.0
FOLLOW_UP_LEAD = "もう少し追加で調べてみますね…"
FOLLOW_UP_FAILED = "追加で調べようとしましたが、うまくいきませんでした。"
# 利用者には見せない指示（会話の履歴には残る）
FOLLOW_UP = ("（自動の追加調査）直前の質問に対して不足している点を、質問に適した情報源で必ず調べ直してください。セッションの日時・会場・形式はsearch_sessions、参加情報や人物の公開情報はsearch_2026・search_past、AWSの更新はsearch_whats_new・search_aws_blog、現地の速報はget_announcementsを使えます。専用検索で根拠が揃えばWeb検索は不要です。不足が残る場合はWebで補います。search_webはBedrockのWeb検索、search_recentはBraveの直近1週間の検索です。鮮度が必要ならsearch_recent、一般の公開情報ならsearch_webを使い、空振りなら検索語や検索先を変えます。直近1週間以外の情報をsearch_recentだけで諦めずsearch_webでも調べます。既に調べた検索語を繰り返さず、不足した属性に絞ります。"
             "利用者が質問した対象と適用条件を引き継ぎ、検索結果の各記述がその対象・条件についての根拠か照合します。"
             "私自身のことなら本名「御田稔」を検索語に入れます。"
             "この回答は直前の回答全体と置き換わります。最初の質問に答える独立した回答として、"
             "対象と根拠を確認できた元の要点と追加情報をまとめ直し、誤りがあれば訂正して、60〜120字・2〜3文（最大150字）にしてください。"
             "前置き・謝罪・調査経緯は書かず、出典は該当する文の末尾に付けます。"
             "Web でも見つからなければ、既に分かっている要点を残し、未確認の点だけを短く伝えます。")



class ToolLimit(HookProvider):
    """1回の質問でツールを呼ぶ回数に上限を付ける。"""

    def register_hooks(self, registry: HookRegistry, **kwargs):
        registry.add_callback(BeforeToolCallEvent, self.check)

    def check(self, event: BeforeToolCallEvent):
        turn = tools.Turn.current
        if turn is None:
            return
        turn.tool_calls += 1
        turn.tools_used.append(event.tool_use["name"])
        if turn.tool_calls > MAX_TOOL_CALLS:
            event.cancel_tool = "ツールを呼べる回数の上限に達した。ここまでに集めた情報で答える。"


def _system(shelf, current, memories=None):
    # 先頭を毎問変えると、その後ろの会話キャッシュも無効になる。
    return [{"text": prompt.BASE}, {"cachePoint": {"type": "default"}}]


def _question_prompt(conv, question, memories):
    context = prompt.dynamic(conv.shelf, conv.current, memories)
    if context == getattr(conv, "last_context", None):
        return question
    conv.last_context = context
    return question + "\n\n[アプリが添えた会話の補助情報。今回の質問は上の本文]\n" + context


def _model(model_id, max_tokens, effort):
    # ツールの定義と会話はキャッシュする。システムプロンプトは変わらない部分の後ろに自前で cachePoint を置く
    return usage.MeteredModel(model_id=model_id, max_tokens=max_tokens,
                        cache_config=CacheConfig(strategy="auto", system_prompt_ttl=False, tools_ttl=True),
                        additional_request_fields={"output_config": {"effort": effort}})


class Conversation:
    """1つの会話（会話ID）の状態。Runtime は会話ごとに別の入れ物なので、実際には1つだけ持つ。"""

    def __init__(self, actor, session, snapshot):
        _require_configuration()
        state = (snapshot or {}).get("state", {})
        self.actor = actor
        self.session = session
        self.shelf = state.get("shelf", [])
        self.current = state.get("current", "")
        self.turns = state.get("turns", 0)
        self.last_context = state.get("last_context")
        self.topic_start = state.get("topic_start", 0)
        self.last_prebaked = state.get("last_prebaked")  # 直前に事前回答で答えた用意した質問（深掘りの回答を使う鍵）
        self.lock = threading.Lock()
        self.agent = Agent(
            model=_model(MODEL_MAIN, 4000, MAIN_EFFORT),
            tools=tools.ALL,
            system_prompt=_system(self.shelf, self.current),
            messages=(snapshot or {}).get("messages", []),
            callback_handler=None,
            conversation_manager=SummarizingConversationManager(
                summary_ratio=0.3, preserve_recent_messages=10,
                summarization_agent=Agent(model=_model(MODEL_AUX, 2000, "low"),
                                          system_prompt=topics.SUMMARY_GROUNDING, callback_handler=None)),
            hooks=[ToolLimit()],
            trace_attributes={"session.id": session, "user.id": actor},
        )

    def state(self):
        return {"shelf": self.shelf, "current": self.current, "turns": self.turns, "last_context": self.last_context, "topic_start": self.topic_start, "last_prebaked": self.last_prebaked}


CONVERSATIONS: dict[str, Conversation] = {}


def _warm_up():
    """会話ごとに新しい入れ物が立つので、最初の質問が来る前にツールの接続と控えの読み込みを済ませる。"""
    for name, step in (("mcp", tools.mcp), ("catalog", catalog.snapshot)):
        try:
            step()
        except Exception as e:
            print(json.dumps({"warm_up_error": name, "detail": type(e).__name__}))


# V2 は最初の正常な ping の状態を複製する。接続・スレッドは要求が届いてから作る。


def _conversation(actor, session):
    conv = CONVERSATIONS.get(session)
    if conv is None:
        try:
            snap = store.load_snapshot(actor, session)
        except Exception as e:
            print(json.dumps({"restore_error": type(e).__name__}))
            snap = None
        conv = CONVERSATIONS[session] = Conversation(actor, session, snap)
    return conv


def _is_question(msg):
    return msg["role"] == "user" and any("text" in c for c in msg["content"])


def _text(msg):
    return "".join(c.get("text", "") for c in msg["content"]).split("\n\n[アプリが添えた会話の補助情報。", 1)[0]


def _transcript(messages):
    lines = []
    for m in messages:
        t = _text(m).strip()
        if t:
            lines.append(("利用者: " if m["role"] == "user" else "エージェント: ") + t)
    return "\n".join(lines)


def _after_answer(conv, task_id, turn_start, judged, row):
    """返し終えた後の裏の処理。終わるまで会話の鍵を持ち、次の質問はそれを待つ。"""
    ledger = row.pop("_usage")
    token = usage.CURRENT.set(ledger)
    phase_token = usage.PHASE.set("summary")
    try:
        if row.get("status") == "error":
            return
        messages = conv.agent.messages
        # 過去の検索本文は次の要約まで保持する。毎問の書換えでキャッシュを壊さない。
        decision = judged["decision"]
        trim = turn_start > 24 or len(json.dumps(messages, ensure_ascii=False)) > 60000
        row["context_trimmed"] = False
        if decision in ("new", "back") and turn_start > 0:
            old = messages[getattr(conv, "topic_start", 0):turn_start]
            summary = topics.summarize(conv.current, _transcript(old))
            conv.shelf = [s for s in conv.shelf if s["heading"] != conv.current]
            conv.shelf.append({"heading": conv.current, "summary": summary})
            # 短い会話は先頭を保ち、話題が変わっただけではキャッシュを作り直さない。
            conv.topic_start = turn_start
            if trim:
                conv.agent.messages = messages[turn_start:]
                conv.topic_start = 0
                conv.last_context = None
                row["context_trimmed"] = True
        elif turn_start > 0 and trim:
            # 同じ話題が長く続いたら、古い3割を要約へ置き換える（直近は残す）
            cut = next(i for i in range(int(turn_start * 0.3), turn_start + 1) if _is_question(messages[i]))
            if cut > 0:
                heading = "これまでの会話" if getattr(conv, "topic_start", 0) > 0 else conv.current
                summary = topics.summarize(heading, _transcript(messages[:cut]))
                conv.shelf.append({"heading": f"{heading}（前半）", "summary": summary})
                conv.agent.messages = messages[cut:]
                conv.topic_start = max(0, getattr(conv, "topic_start", 0) - cut)
                conv.last_context = None
                row["context_trimmed"] = True
        if decision == "back":
            conv.current = judged["heading"] or conv.current
        elif decision == "new":
            conv.current = judged["heading"]
        conv.shelf = conv.shelf[-12:]
        conv.turns += 1
        store.save_snapshot(conv.actor, conv.session, conv.agent.messages, conv.state())
        if row.get("user"):
            store.remember_turn(conv.actor, conv.session, row["question"], row["answer"])
    except Exception as e:
        print(json.dumps({"after_answer_error": type(e).__name__, "detail": str(e)[:300]}))
    finally:
        try:
            store.log_turn({**row, "pk": conv.session, "topic": conv.current,
                            "turn": getattr(conv, "turns", 0), "usage": ledger.snapshot()})
        except Exception as e:
            print(json.dumps({"turn_log_error": type(e).__name__}))
        usage.CURRENT.reset(token)
        usage.PHASE.reset(phase_token)
        conv.lock.release()
        app.complete_async_task(task_id)


def _tool_label(name, question, current):
    if name in ("search_2026", "search_past"):
        return knowledge_label(name, question, current or "")
    return LABELS.get(name, "調べています")


async def _prebaked_turn(conv, turn, pb, model_question, started, base, ledger, session):
    """用意した回答を本番と同じ速さで流し、会話の履歴・記録・裏の処理は本番と同じにする。"""
    ledger.prebaked = True
    question = pb["q"]
    turn_start = len(conv.agent.messages)
    judged = prebaked.judged(pb, conv.current)
    # 深掘りの後の候補は用意していないので、本番どおり Haiku で作る
    nexts = (asyncio.create_task(asyncio.to_thread(topics.suggest_next, conv.current, conv.shelf, question))
             if pb["kind"] == "follow" else None)
    yield {"type": "topic", "heading": judged["heading"], "changed": judged["decision"] != "same"}
    first_ms = None
    async for ev in prebaked.play(pb):
        if ev["type"] == "tool":
            turn.tools_used.append(ev["name"])
            ev = {**ev, "label": _tool_label(ev["name"], question, conv.current)}
        elif first_ms is None:
            first_ms = store.elapsed_ms(started)
        yield ev
    answer = pb["answer"]
    conv.agent.messages.extend([{"role": "user", "content": [{"text": model_question}]},
                                {"role": "assistant", "content": [{"text": answer}]}])
    shown = tools.map_payload(pb["map"]) if pb.get("map") else None
    if shown:
        yield {"type": "map", **shown}
    if nexts is None:
        items = prebaked.suggestions(pb)
    else:
        try:
            items = await asyncio.wait_for(asyncio.shield(nexts), 1.0)
        except Exception:
            items = []
    if items:
        yield {"type": "suggest", "items": items}
    if not shown:
        preview = await link_preview.for_answer(answer)
        if preview:
            yield {"type": "preview", **preview}
    yield {"type": "done"}
    conv.last_prebaked = question if pb["kind"] == "first" else None
    row = {**base, "question": question, "answer": answer, "first_ms": first_ms, "total_ms": store.elapsed_ms(started),
           "tools": turn.tools_used, "catalog": None, "model": "prebaked", "effort": None, "status": "ok",
           "prebaked": pb["kind"], "_usage": ledger, "initial_answer": answer,
           "research": {"decision_status": "skipped", "outcome": "not_started", "initial_tools": list(turn.tools_used)},
           "follow_up": False}
    task_id = app.add_async_task("after_answer", {"session": session})
    threading.Thread(target=_after_answer, args=(conv, task_id, turn_start, judged, row), daemon=True).start()


@app.entrypoint
async def invoke(payload, context):
    started = time.time()
    session = getattr(context, "session_id", None) or "local"
    # ログインした人は利用者のハッシュで覚え、端末をまたいでも同じ人として扱う（長期記憶もこの名前の下に残る）
    user = str(payload.get("user") or "")[:32]
    actor = f"user-{user}" if user else str(payload.get("anon") or "anonymous")[:64]
    question = str(payload.get("prompt", "")).strip()[:1000]
    tid = str(payload.get("tid") or "")[:40]  # 画面が質問ごとに作る番号（回答の評価を、この行へ結び付ける）
    via = str(payload.get("via") or "")[:10]  # 質問の出どころ（typed・ai・starter・link）
    if payload.get("warmup"):
        # ページを開いたときの先読み。入れ物が起き、会話の控えも戻しておく
        await asyncio.gather(asyncio.to_thread(_warm_up), asyncio.to_thread(_conversation, actor, session))
        yield {"type": "done"}
        return
    if not question:
        yield {"type": "error", "message": "質問を入れてから送ってください。"}
        yield {"type": "done"}
        return

    conv = _conversation(actor, session)
    await asyncio.to_thread(conv.lock.acquire)
    handed_off = False
    ledger = usage.Ledger()
    usage_token = usage.CURRENT.set(ledger)
    try:
        turn = tools.Turn()
        turn.workload_token = BedrockAgentCoreContext.get_workload_access_token()
        tools.Turn.current = turn
        turn_start = len(conv.agent.messages)
        memories = []
        if user:
            try:
                memories = await asyncio.to_thread(store.recall, actor, question)
            except Exception as e:
                print(json.dumps({"recall_error": type(e).__name__, "detail": str(e)[:200]}))
        previous_context = getattr(conv, "last_context", None)
        model_question = _question_prompt(conv, question, memories)
        pb = prebaked.lookup(question, getattr(conv, "last_prebaked", None))
        conv.last_prebaked = None
        if pb is not None:
            base = {"anon": actor, "user": user or None, "memories": len(memories),
                    **({"tid": tid} if tid else {}), **({"via": via} if via else {})}
            async for ev in _prebaked_turn(conv, turn, pb, model_question, started, base, ledger, session):
                yield ev
            handed_off = True
            return
        judge = asyncio.create_task(asyncio.to_thread(topics.judge, conv.current, conv.shelf, question))
        nexts = asyncio.create_task(asyncio.to_thread(topics.suggest_next, conv.current, conv.shelf, question))
        topic_sent, seen_tools, first_ms, answer, failed, maps_sent = False, set(), None, [], False, 0

        def topic_event(j):
            changed = j["decision"] != "same"
            return {"type": "topic", "heading": j["heading"], "changed": changed}

        def tool_event(use):
            name = use.get("name")
            return {"type": "tool", "name": name, "label": _tool_label(name, question, conv.current)}

        try:
            async for ev in conv.agent.stream_async(model_question):
                if "data" in ev and ev["data"]:
                    if first_ms is None:
                        first_ms = store.elapsed_ms(started)
                    answer.append(ev["data"])
                    yield {"type": "text", "text": ev["data"]}
                use = ev.get("current_tool_use")
                if use and use.get("toolUseId") and use["toolUseId"] not in seen_tools:
                    seen_tools.add(use["toolUseId"])
                    yield tool_event(use)
                while turn.maps[maps_sent:]:
                    yield {"type": "map", **turn.maps[maps_sent]}
                    maps_sent += 1
                if not topic_sent and judge.done():
                    topic_sent = True
                    yield topic_event(judge.result())
        except Exception as e:
            failed = True
            print(json.dumps({"agent_error": type(e).__name__, "detail": str(e)[:300]}))
            yield {"type": "error", "message": SORRY}

        initial_answer = "".join(answer)
        research_audit = {"decision_status": "skipped", "outcome": "not_started",
                          "initial_tools": list(turn.tools_used)}
        # 最初の回答は残し、追加調査が最後まで成功してから要約で丸ごと置き換える
        if not failed and "".join(answer).strip() and await asyncio.to_thread(
                research.needed, question, "".join(answer),
                _transcript(conv.agent.messages[-8:]), turn.tools_used, research_audit):
            followed = True
            yield {"type": "follow_up", "text": FOLLOW_UP_LEAD}
            turn.tool_calls = min(turn.tool_calls, MAX_TOOL_CALLS - 3)
            revised = []
            follow_up_start = len(conv.agent.messages)
            follow_up_tools_start = len(turn.tools_used)
            try:
                phase_token = usage.PHASE.set("follow_up")
                async for ev in conv.agent.stream_async(FOLLOW_UP):
                    if "data" in ev and ev["data"]:
                        revised.append(ev["data"])
                    use = ev.get("current_tool_use")
                    if use and use.get("toolUseId") and use["toolUseId"] not in seen_tools:
                        seen_tools.add(use["toolUseId"])
                        yield tool_event(use)
                if not research.SEARCH_TOOLS & set(turn.tools_used[follow_up_tools_start:]):
                    raise ValueError("follow-up skipped research")
                if not "".join(revised).strip():
                    raise ValueError("empty follow-up answer")
                answer = revised
                research_audit["outcome"] = "replaced"
                yield {"type": "replace", "text": "".join(answer)}
            except Exception as e:
                research_audit["outcome"] = "failed"
                research_audit["error_type"] = type(e).__name__
                print(json.dumps({"follow_up_error": type(e).__name__, "detail": str(e)[:300]}))
                conv.agent.messages = conv.agent.messages[:follow_up_start]
                yield {"type": "follow_up", "text": FOLLOW_UP_FAILED}
            finally:
                research_audit["follow_up_tools"] = list(turn.tools_used[follow_up_tools_start:])
                usage.PHASE.reset(phase_token)
        else:
            followed = False

        judged = await judge
        if not topic_sent:
            yield topic_event(judged)
        for m in turn.maps[maps_sent:]:
            yield {"type": "map", **m}

        catalog_state = None
        preview_task = asyncio.create_task(link_preview.for_answer(''.join(answer))) if not failed and not turn.maps else None
        if turn.verify is not None and not failed:
            try:
                result = await asyncio.wait_for(asyncio.wrap_future(turn.verify), VERIFY_WAIT)
            except Exception:
                result = None
            if result:
                lines = catalog.describe(result)
                catalog_state = "changed" if lines else "verified"
                if lines:
                    yield {"type": "catalog_update", "at": result["checked_at"], "lines": lines}
            else:
                catalog_state = "unchecked"
        if not failed:
            # 受付直後に並行して作った候補を1回だけ使う。
            try:
                items = await asyncio.wait_for(asyncio.shield(nexts), 1.0)
            except Exception:
                items = []
            if items:
                yield {"type": "suggest", "items": items}
        if preview_task:
            preview = await preview_task
            if preview:
                yield {"type": "preview", **preview}
        yield {"type": "done"}

        if failed:
            # 途中で失敗した質問は会話に残さない（壊れたやり取りが次の質問に混ざらないように）
            conv.agent.messages = conv.agent.messages[:turn_start]
            conv.last_context = previous_context
        row = {"anon": actor, "user": user or None, "memories": len(memories), "question": question, "answer": "".join(answer), "first_ms": first_ms,
               "total_ms": store.elapsed_ms(started), "tools": turn.tools_used, "catalog": catalog_state,
               "model": MODEL_MAIN, "effort": MAIN_EFFORT, "status": "error" if failed else "ok",
               "_usage": ledger, "initial_answer": initial_answer, "research": research_audit, "follow_up": followed, **({"tid": tid} if tid else {}), **({"via": via} if via else {})}
        task_id = app.add_async_task("after_answer", {"session": session})
        threading.Thread(target=_after_answer, args=(conv, task_id, turn_start, judged, row), daemon=True).start()
        handed_off = True
    finally:
        usage.CURRENT.reset(usage_token)
        tools.Turn.current = None
        if not handed_off:
            conv.lock.release()


def _require_configuration():
    if not all((prompt.BASE, research.CHECK, topics.JUDGE, topics.NEXT, topics.SUMMARY, topics.SUMMARY_GROUNDING)):
        raise RuntimeError("agent の *.local.txt を設定してください")


if __name__ == "__main__":
    _require_configuration()
    # 新しい会話の最初の返事が遅いときの切り分け用に、読み込みにかかった時間を残す
    print(json.dumps({"boot": "imports_done", "seconds": round(time.time() - _BOOT, 2)}), flush=True)
    app.run()
