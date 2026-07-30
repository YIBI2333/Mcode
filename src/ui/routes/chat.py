"""POST /chat — SSE chat endpoint."""
import sys, os, re, asyncio
import queue as thread_queue
import json as _json
import logging
from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .shared import _executor, clear_tts_cache, do_tts_sentence, sseline, reset_cancel, request_cancel, is_cancelled, touch_heartbeat

_log = logging.getLogger(__name__)

router = APIRouter()

# ── OCR（进程内，轻量复用 easyocr 模型） ──
_easyocr_reader = None

def _get_ocr():
    global _easyocr_reader
    if _easyocr_reader is not None:
        return _easyocr_reader
    try:
        import easyocr
        _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        model_dir = os.path.join(_proj_root, "easyocr_models")
        os.makedirs(model_dir, exist_ok=True)
        _easyocr_reader = easyocr.Reader(["ch_sim"], gpu=False, verbose=False,
                                          model_storage_directory=model_dir, download_enabled=True)
    except Exception as e:
        print(f"[chat] OCR 加载失败: {e}", file=sys.stderr, flush=True)
        _easyocr_reader = False
    return _easyocr_reader

def _ocr_image_file(path: str) -> str:
    """对图片文件做 OCR，返回识别文字"""
    reader = _get_ocr()
    if not reader:
        return ""
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(path)
        w, h = img.size
        if w > 1280:
            img = img.resize((1280, int(h * 1280 / w)))
        arr = np.array(img.convert("RGB"))
        items = reader.readtext(arr, detail=1)
        lines = [item[1] for item in items if item[2] > 0.4]
        return "\n".join(lines[:60]) if lines else ""
    except Exception:
        return ""


@router.get("/chat/greet-pending")
async def greet_pending():
    """前端查询是否有待执行的启动打招呼"""
    from src.infra.messages import check_and_clear_greet_flag
    pending = check_and_clear_greet_flag()
    return {"greet": pending}


@router.get("/chat/heartbeat-pending")
async def heartbeat_pending():
    """前端轮询：检查是否有待发送的心跳消息"""
    from .shared import check_and_clear_heartbeat
    return check_and_clear_heartbeat()


@router.post("/chat/cancel")
async def chat_cancel():
    """Cancel the currently running generation."""
    request_cancel()
    return {"ok": True, "msg": "cancellation requested"}


@router.post("/chat")
async def chat(req: Request):
    """Chat endpoint — SSE. Tools appear immediately; sentences sent with TTS audio together."""
    body = await req.json()
    text = body.get("message", "")
    hidden = body.get("hidden", False)  # 隐藏消息：不记入显示历史
    # 支持单个 image 或数组 images
    image_paths = body.get("images", None)
    if image_paths is None:
        single = body.get("image", None)
        image_paths = [single] if single else []

    img_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    text_exts = {".txt", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".html", ".css",
                 ".md", ".yaml", ".yml", ".xml", ".csv", ".log", ".cfg", ".ini", ".toml",
                 ".sh", ".bat", ".ps1", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".rb",
                 ".lua", ".sql", ".r", ".m", ".swift", ".kt", ".scala", ".php", ".vue", ".svelte"}

    # 读取所有上传文件，内容直接拼接进消息
    for image_path in image_paths:
        if not image_path or not os.path.isfile(image_path):
            continue
        ext = os.path.splitext(image_path)[1].lower()
        if ext in img_exts:
            # MiniMax 视觉分析 + OCR 文字提取
            import base64 as _b64, re as _re
            import config.api_config as _vcfg
            from openai import OpenAI as _vOpenAI
            fname = os.path.basename(image_path)
            parts = []
            # 视觉分析
            try:
                mime = {"png":"image/png","jpg":"image/jpeg","jpeg":"image/jpeg",
                        "webp":"image/webp","gif":"image/gif","bmp":"image/bmp"}.get(ext[1:],"image/png")
                with open(image_path, "rb") as f:
                    img_b64 = _b64.b64encode(f.read()).decode()
                vc = _vOpenAI(api_key=_vcfg.VISION_LLM_KEY, base_url=_vcfg.VISION_LLM_BASE_URL)
                vr = vc.chat.completions.create(
                    model=_vcfg.VISION_LLM_MODEL,
                    messages=[{"role":"user","content":[
                        {"type":"text","text":"请详细描述这张图片的内容"},
                        {"type":"image_url","image_url":{"url":f"data:{mime};base64,{img_b64}"}},
                    ]}],
                    temperature=0.3,
                )
                vcontent = (vr.choices[0].message.content or "").strip()
                vcontent = _re.sub(r'<think>.*?</think>\s*', '', vcontent, flags=_re.DOTALL).strip()
                if vcontent:
                    parts.append(f"[图片「{fname}」内容]\n{vcontent}")
            except Exception as e:
                print(f"[chat] 视觉分析失败: {e}", file=sys.stderr, flush=True)
            # OCR 补充
            ocr_text = _ocr_image_file(image_path)
            if ocr_text:
                parts.append(f"[图中文字]\n{ocr_text}")
            if parts:
                text = (text + "\n\n" + "\n\n".join(parts)) if text else "\n\n".join(parts)
        elif ext in text_exts and os.path.getsize(image_path) < 500 * 1024:
            try:
                with open(image_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if len(content) > 8000:
                    content = content[:8000] + "\n...（文件过长，已截断）"
                text = (text + "\n\n" + content) if text else content
            except Exception:
                pass

    clear_tts_cache()
    reset_cancel()
    touch_heartbeat(from_heartbeat=hidden)  # 心跳消息不重置间隔

    try:
        from src.core.chat import generate_reply
    except Exception as e:
        return {"error": f"import failed: {e}"}

    loop = asyncio.get_event_loop()

    async def event_stream():
        q: thread_queue.Queue = thread_queue.Queue()
        import time as _time_mod
        _start_ts = _time_mod.time()
        _HEARTBEAT_S = 15
        _TIMEOUT_S = 120

        def _run():
            try:
                for chunk in generate_reply("web_user", text, cancel_check=is_cancelled):
                    q.put(("chunk", chunk))
                q.put(("done", None))
            except Exception as e:
                import traceback
                traceback.print_exc(file=sys.stderr)
                q.put(("error", str(e)))

        loop.run_in_executor(_executor, _run)

        yield sseline("thinking", '""')

        from src.infra.messages import append_user_message, append_assistant_sentences, append_tool_event
        append_user_message(text, hidden=hidden)
        pending_tts = []        # (idx, text, asyncio.Task) — TTS 在句子到达时立即启动
        collected_tools = []

        def _get_emotion(sent: str):
            try:
                from src.infra.live2d_action import get_emotion_and_intensity
                return get_emotion_and_intensity(sent)
            except Exception:
                return ("neutral", 0.3)

        while True:
            try:
                kind, value = await loop.run_in_executor(
                    _executor, partial(q.get, timeout=_HEARTBEAT_S)
                )
            except thread_queue.Empty:
                # 检查是否被取消（心跳间隔也检查）
                if is_cancelled():
                    for _, _, t in pending_tts:
                        if t is not None and not t.done():
                            t.cancel()
                    yield sseline("cancelled", _json.dumps({"msg": "用户中断"}))
                    return
                if _time_mod.time() - _start_ts > _TIMEOUT_S:
                    yield sseline("error", _json.dumps({"msg": "request timeout"}))
                    return
                yield ": heartbeat\n\n"
                continue

            if kind == "done":
                for tag, content in collected_tools:
                    append_tool_event(tag, content)

                # TTS 边完成边发送：跳过已通过 [[speak]] 发过的句子（idx == -1）
                for idx, sent, task in pending_tts:
                    if idx == -1:
                        continue  # 已通过 speak 事件即时发送
                    if task is not None:
                        await task
                    if task is None:
                        data = {"text": sent, "index": idx, "tts_url": "", "duration_ms": 0,
                                "phonemes": [], "emotion": "neutral", "intensity": 0, "motion": "neutral"}
                    else:
                        emotion, intensity = _get_emotion(sent)
                        try:
                            tts = task.result()
                        except Exception:
                            tts = None
                        if tts:
                            data = {"text": sent, "index": idx, "tts_url": tts["tts_url"],
                                    "duration_ms": tts["duration_ms"], "phonemes": tts["phonemes"],
                                    "emotion": emotion, "intensity": intensity, "motion": emotion}
                        else:
                            data = {"text": sent, "index": idx, "tts_url": "",
                                    "duration_ms": max(800, min(len(sent) * 80, 12000)),
                                    "phonemes": [], "emotion": emotion, "intensity": intensity, "motion": emotion}
                    yield sseline("sentence", _json.dumps(data, ensure_ascii=False))

                if pending_tts:
                    # 去重：[[speak]] 和 [[sentence:N]] 可能有重叠
                    seen = set()
                    unique = []
                    for _, s, _ in pending_tts:
                        if s not in seen:
                            seen.add(s)
                            unique.append(s)
                    append_assistant_sentences(unique, hidden=hidden)
                yield sseline("done", '""')
                return

            if kind == "error":
                yield sseline("error", _json.dumps({"msg": value}))
                return

            chunk = value

            if chunk.startswith("[[error]]"):
                err_text = chunk[9:].strip()
                yield sseline("error", _json.dumps({"msg": err_text}))
                continue

            # 处理取消标记（generate_reply 内部检测到取消时发出）
            if chunk.startswith("[[cancelled]]"):
                for _, _, t in pending_tts:
                    if t is not None and not t.done():
                        t.cancel()
                yield sseline("cancelled", _json.dumps({"msg": chunk[13:].strip() or "用户中断"}))
                return

            # ── 新事件类型：思考、计划、进度、反思 ──
            if chunk.startswith("[[thinking]]"):
                msg_text = chunk[12:].strip()
                yield sseline("thinking", _json.dumps({"text": msg_text}, ensure_ascii=False))
                continue
            if chunk.startswith("[[plan]]"):
                msg_text = chunk[8:].strip()
                yield sseline("plan", _json.dumps({"text": msg_text}, ensure_ascii=False))
                continue
            if chunk.startswith("[[progress]]"):
                msg_text = chunk[12:].strip()
                yield sseline("progress", _json.dumps({"text": msg_text}, ensure_ascii=False))
                continue
            if chunk.startswith("[[reflect]]"):
                msg_text = chunk[11:].strip()
                yield sseline("reflect", _json.dumps({"text": msg_text}, ensure_ascii=False))
                continue

            # Tool events -> yield immediately (no TTS for tool results)
            if chunk.startswith("[[tool:") or chunk.startswith("[[tool_result:"):
                if chunk.startswith("[[tool_result:"):
                    result_text = chunk[14:].rsplit("]]", 1)[0].strip()
                    data = {"tag": "result", "text": result_text}
                    collected_tools.append(("result", result_text))
                    yield sseline("tool", _json.dumps(data, ensure_ascii=False))
                else:
                    inner = chunk[7:].rsplit("]]", 1)[0].strip()
                    m_tool = re.match(r'^\[(\d+)\]\s*(.*)', inner)
                    tool_text = m_tool.group(2) if m_tool else inner
                    data = {"tag": "call", "text": tool_text}
                    collected_tools.append(("call", tool_text))
                    yield sseline("tool", _json.dumps(data, ensure_ascii=False))
                continue

            # step_desc -> yield with TTS immediately (unless disabled)
            m_step = re.match(r'^\[\[step_desc:(-?\d+)\]\](.*)', chunk)
            if m_step:
                step_text = m_step.group(2).strip()
                if step_text:
                    from config.settings import TOOL_TTS_ENABLED
                    tts_text = re.sub(r'^步骤\s*\d+\s*[:：]\s*', '', step_text)
                    if TOOL_TTS_ENABLED:
                        tts = await do_tts_sentence(tts_text)
                    else:
                        tts = None
                    data = tts or {"text": step_text, "tts_url": "", "duration_ms": 0, "phonemes": []}
                    data["index"] = max(0, int(m_step.group(1)))
                    yield sseline("step_desc", _json.dumps(data, ensure_ascii=False))
                continue

            if chunk == "[[reply_start]]":
                continue

            # ── 中间回复（speak）→ 即时 TTS + SSE 推送 ──
            if chunk.startswith("[[speak]]"):
                speak_text = chunk[len("[[speak]]"):].strip()
                if speak_text:
                    from src.core.sentence_splitter import split_full_text
                    sentences = split_full_text(speak_text)
                    # 并行启动所有 TTS，哪句先好就先发
                    tts_map = {}  # idx -> asyncio.Task
                    for i, sent in enumerate(sentences):
                        if not sent.strip():
                            continue
                        if sent.startswith('[MD]'):
                            sent = sentences[i] = sent[4:]
                        else:
                            tts_map[i] = asyncio.create_task(do_tts_sentence(sent))
                    # 依次等每句的 TTS（并行跑的，后启动的可能先完成）
                    for i, sent in enumerate(sentences):
                        if not sent.strip():
                            continue
                        task = tts_map.get(i)
                        is_md = task is None
                        if not is_md:
                            try:
                                tts = await task
                            except Exception:
                                tts = None
                        emotion, intensity = _get_emotion(sent) if not is_md else ("neutral", 0)
                        if is_md:
                            data = {"text": sent, "index": -1, "tts_url": "", "duration_ms": 0,
                                    "phonemes": [], "emotion": emotion, "intensity": intensity, "motion": emotion}
                        else:
                            data = {
                                "text": sent, "index": -1,
                                "tts_url": tts["tts_url"] if tts else "",
                                "duration_ms": tts["duration_ms"] if tts else max(800, min(len(sent) * 80, 12000)),
                                "phonemes": tts["phonemes"] if tts else [],
                                "emotion": emotion, "intensity": intensity, "motion": emotion,
                            }
                        yield sseline("speak", _json.dumps(data, ensure_ascii=False))
                        pending_tts.append((-1, sent, None))
                continue

            # Sentence -> 立即启动 TTS（不等 LLM 全部生成完）
            m_sent = re.match(r"^\[\[sentence:(\d+)\]\](.*)", chunk)
            if m_sent:
                sent_idx = int(m_sent.group(1))
                sent = m_sent.group(2)
                if sent:
                    # 代码块/表格 → 不生成 TTS
                    if sent.startswith('[MD]'):
                        sent = sent[4:]
                        pending_tts.append((sent_idx, sent, None))
                    else:
                        task = asyncio.create_task(do_tts_sentence(sent))
                        pending_tts.append((sent_idx, sent, task))
                continue

            stripped = chunk.strip()
            if stripped:
                print(f"[Web] unrecognized chunk (blocked): {stripped[:80]}", file=sys.stderr)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
