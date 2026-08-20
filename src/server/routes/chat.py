"""POST /chat — SSE 聊天端点

事件: sentence / tool / status / done
TTS 策略: 句子产生时在线程内立即通过 run_coroutine_threadsafe 调度到事件循环，
         事件循环只需 await 已在运行的 TTS future，无需额外排队逻辑。
"""

import sys
import asyncio
import re
import queue as thread_queue
import json as _json
import logging
from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.server.routes.shared import (
    _executor, clear_tts_cache, do_tts_sentence, sseline,
    reset_cancel, request_cancel, is_cancelled, touch_heartbeat,
    HEARTBEAT_TAG,
)
from src.agent.pipeline import SentenceEvent, ToolEvent, StatusEvent
from src.agent.vision import preprocess_attachments
from src.memory.images import resolve_image_refs as _resolve_image_refs

_log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/chat/greet-pending")
async def greet_pending():
    from src.memory.compress import check_and_clear_greet_flag
    return {"greet": check_and_clear_greet_flag()}


@router.get("/chat/heartbeat-pending")
async def heartbeat_pending():
    from src.server.routes.shared import check_and_clear_heartbeat
    return check_and_clear_heartbeat()


@router.get("/chat/task-pending")
async def task_pending():
    from src.scheduler.engine import check_and_pop_task
    task = check_and_pop_task()
    if task:
        return {"pending": True, "message": task["message"], "task_name": task["task_name"]}
    return {"pending": False}


@router.post("/chat/cancel")
async def chat_cancel():
    request_cancel()
    return {"ok": True, "msg": "cancellation requested"}

@router.post("/chat/respond")
async def chat_respond(req: Request):
    """Agent 暂停时用户回复"""
    body = await req.json()
    session_id = body.get("session_id", "")
    text = body.get("message", "")
    from src.agent.session import get_session
    session = get_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}
    session.respond(text)
    return {"ok": True, "session_id": session_id}


@router.post("/chat")
async def chat(req: Request):
    body = await req.json()
    text = body.get("message", "")
    hidden = body.get("hidden", False)
    image_paths = body.get("images", None)
    if image_paths is None:
        single = body.get("image", None)
        image_paths = [single] if single else []

    # 心跳消息：作为 system 指令注入（而非用户输入），避免 AI 复述系统话术
    system_note = None
    if hidden and text.startswith(HEARTBEAT_TAG):
        system_note = text[len(HEARTBEAT_TAG):].strip()
        text = ""

    text = preprocess_attachments(text, image_paths)
    clear_tts_cache()
    reset_cancel()
    touch_heartbeat(from_heartbeat=hidden)

    from src.agent.generator import generate_reply

    loop = asyncio.get_event_loop()

    async def event_stream():
        # 线程→异步 的桥梁队列。线程产生的每个 item 是:
        #   ("tool", ToolEvent) / ("status", StatusEvent)
        #   ("sentence", (SentenceEvent, concurrent.futures.Future))  ← TTS 已在跑
        #   ("done", None) / ("error", str)
        q: thread_queue.Queue = thread_queue.Queue()
        import time as _time_mod
        _start_ts = _time_mod.time()

        # ── 在 executor 线程中运行 generate_reply ──
        def _run():
            try:
                print("[SSE] _run started", file=sys.stderr, flush=True)
                count = 0
                for event in generate_reply("web_user", text, cancel_check=is_cancelled,
                                            hidden=hidden, system_note=system_note):
                    count += 1
                    tname = type(event).__name__
                    print(f"[SSE] _run event#{count} {tname}", file=sys.stderr, flush=True)
                    q.put(("event", event))
                print(f"[SSE] _run done, total={count} events", file=sys.stderr, flush=True)
                q.put(("done", None))
            except Exception as e:
                import traceback
                traceback.print_exc(file=sys.stderr)
                q.put(("error", str(e)))

        loop.run_in_executor(_executor, _run)

        from src.memory.store import append_user_message, append_assistant_sentences, append_tool_event, append_status_event
        if text:
            append_user_message(text, hidden=hidden)

        all_sentences = []
        collected_tools = []
        collected_status_early = []   # task-start / plan（工具之前）
        collected_status_late = []    # task-end（回答之后）
        pending_tts = []

        def _persist_history():
            """按时间线落盘：任务开始/计划 → 工具 → 回答 → 任务完成总结"""
            if not hidden:
                for type_, content, meta in collected_status_early:
                    append_status_event(type_, content, meta)
            for tag, content in collected_tools:
                append_tool_event(tag, content)
            if all_sentences:
                append_assistant_sentences(all_sentences, hidden=hidden)
            if not hidden:
                for type_, content, meta in collected_status_late:
                    append_status_event(type_, content, meta)

        async def _drain():
            """逐句排出已完成的 TTS，遇到未完成就停（保留顺序）"""
            nonlocal pending_tts
            while pending_tts:
                ev, t, _ = pending_tts[0]
                if not t.done():
                    return  # 第一句 TTS 还没好，后面的都等
                pending_tts.pop(0)
                try: tts = await t
                except: tts = None
                emotion, intensity = _get_emotion(ev.text)
                if tts:
                    ev.tts_url = tts["tts_url"]
                    ev.duration_ms = tts["duration_ms"]
                    ev.phonemes = tts["phonemes"]
                else:
                    ev.duration_ms = max(800, min(len(ev.text) * 80, 12000))
                ev.emotion = emotion
                ev.intensity = intensity
                all_sentences.append(ev.text)
                yield sseline("sentence", _json.dumps(ev.to_dict(), ensure_ascii=False))

        def _wake_on_tts_done(t):
            """任意 TTS 完成 → 放入唤醒哨兵，让主循环立即 _drain()"""
            try:
                q.put(("tts-wake", None))
            except Exception:
                pass

        _stream_done = False
        while True:
            _poll = 0.5 if pending_tts else 15
            try:
                kind, value = await loop.run_in_executor(
                    _executor, partial(q.get, timeout=_poll))
            except thread_queue.Empty:
                async for line in _drain():
                    yield line
                # Agent 暂停等待用户输入
                from src.server.routes.shared import get_and_clear_user_prompt
                if prompt := get_and_clear_user_prompt():
                    yield sseline("user-prompt", _json.dumps(prompt.to_dict(), ensure_ascii=False))
                if is_cancelled():
                    for _, t, _ in pending_tts:
                        if not t.done(): t.cancel()
                    yield sseline("status", _json.dumps({"type": "cancelled", "text": "用户中断"}, ensure_ascii=False))
                    return
                if _stream_done and not pending_tts:
                    _persist_history()
                    yield sseline("done", '""')
                    return
                if _time_mod.time() - _start_ts > 120:
                    yield sseline("status", _json.dumps({"type": "error", "text": "request timeout"}, ensure_ascii=False))
                    return
                yield ": heartbeat\n\n"
                continue

            if kind == "done":
                _stream_done = True
                # 逐个收集句子。TTS 若已就绪立即使用；未就绪只给少量缓冲，
                # 累计超过 _MAX_TTS_GRACE 后放弃等待（直接落回时长估算），
                # 避免后端 TTS 卡住/断网时整个 SSE 流长时间冻结。
                _TTS_GRACE = 0.0
                _MAX_TTS_GRACE = 20.0
                for ev, t, _ in pending_tts:
                    tts = None
                    if t.done():
                        try:
                            tts = t.result()
                        except (asyncio.CancelledError, Exception):
                            tts = None
                    elif _TTS_GRACE < _MAX_TTS_GRACE:
                        _g = min(_MAX_TTS_GRACE - _TTS_GRACE, 3.0)
                        try:
                            tts = await asyncio.wait_for(t, timeout=_g)
                        except (asyncio.TimeoutError, Exception):
                            tts = None
                        _TTS_GRACE += _g
                    emotion, intensity = _get_emotion(ev.text)
                    if tts:
                        ev.tts_url = tts["tts_url"]
                        ev.duration_ms = tts["duration_ms"]
                        ev.phonemes = tts["phonemes"]
                    else:
                        ev.duration_ms = max(800, min(len(ev.text) * 80, 12000))
                    ev.emotion = emotion
                    ev.intensity = intensity
                    all_sentences.append(ev.text)
                    yield sseline("sentence", _json.dumps(ev.to_dict(), ensure_ascii=False))
                pending_tts = []
                _persist_history()
                yield sseline("done", '""')
                return

            if kind == "tts-wake":
                # TTS 完成后主动唤醒，立即排出就绪句子
                async for line in _drain():
                    yield line
                continue

            if kind == "error":
                yield sseline("status", _json.dumps({"type": "error", "text": value}, ensure_ascii=False))
                return

            event = value

            if isinstance(event, ToolEvent):
                collected_tools.append((event.tag, event.text))
                yield sseline("tool", _json.dumps(event.to_dict(), ensure_ascii=False))
                continue

            if isinstance(event, StatusEvent):
                data = event.to_dict()
                # 收集任务状态事件，供结束后落盘到历史
                if event.type == "task-start":
                    collected_status_early.append(("task-start", event.text, None))
                elif event.type == "plan":
                    collected_status_early.append(("plan", event.text, None))
                elif event.type == "task-end":
                    collected_status_late.append(("task-end", event.text, event.meta))
                if event.type == "step":
                    from config.settings import TOOL_TTS_ENABLED
                    if TOOL_TTS_ENABLED:
                        import re as _re
                        tts_text = _re.sub(r'^步骤\s*\d+\s*[:：]\s*', '', event.text)
                        try:
                            tts = await do_tts_sentence(tts_text)
                            if tts:
                                data["tts_url"] = tts["tts_url"]
                                data["duration_ms"] = tts["duration_ms"]
                                data["phonemes"] = tts["phonemes"]
                        except Exception:
                            pass
                yield sseline("status", _json.dumps(data, ensure_ascii=False))
                continue

            if isinstance(event, SentenceEvent):
                # 解析图片引用：[图片:key] → markdown 图片（前端渲染）
                event.text = _resolve_image_refs(event.text)
                # 代码块/表格（[MD] 前缀，或含三反引号/波浪线围栏）不合成语音
                if event.text.startswith('[MD]') or '```' in event.text or '~~~' in event.text:
                    event.text = event.text[4:] if event.text.startswith('[MD]') else event.text
                    event.duration_ms = max(500, min(len(event.text) * 50, 8000))
                    event.emotion, event.intensity = _get_emotion(event.text)
                    all_sentences.append(event.text)
                    yield sseline("sentence", _json.dumps(event.to_dict(), ensure_ascii=False))
                    continue
                # TTS 文本去掉图片语法（避免念出 url）
                tts_text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', event.text)
                task = asyncio.create_task(do_tts_sentence(tts_text))
                task.add_done_callback(_wake_on_tts_done)
                pending_tts.append((event, task, _time_mod.time()))
                async for line in _drain():
                    yield line
                continue

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════

def _get_emotion(sent: str) -> tuple:
    try:
        from src.agent.emotion import detect_emotion
        return detect_emotion(sent)
    except Exception:
        return ("neutral", 0.3)


