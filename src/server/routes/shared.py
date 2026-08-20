"""Shared state and helpers for route modules."""
import os, sys, re, time, asyncio
import threading
import logging
from concurrent.futures import ThreadPoolExecutor

_log = logging.getLogger(__name__)

# ---- Thread pool ----
_executor = ThreadPoolExecutor(max_workers=4)

# ---- Cancel mechanism ----
_cancel_event = threading.Event()
_cancel_lock = threading.Lock()

def request_cancel():
    """Signal cancellation of the current generation."""
    with _cancel_lock:
        _cancel_event.set()
    # 解除正在等待用户输入的 agent 会话（ask_user 工具会挂在 wait_for_user 上），
    # 避免取消时工作线程永久阻塞
    try:
        from src.agent.session import _sessions
        for s in list(_sessions.values()):
            s.cancel()
    except Exception:
        pass

def reset_cancel():
    """Reset cancel flag before a new generation starts."""
    with _cancel_lock:
        _cancel_event.clear()

def is_cancelled() -> bool:
    """Check if cancellation has been requested (thread-safe)."""
    return _cancel_event.is_set()

# ---- User prompt mechanism (ask_user 工具 → 前端提问) ----
_user_prompt = None
_user_prompt_lock = threading.Lock()

def set_user_prompt(prompt):
    global _user_prompt
    with _user_prompt_lock:
        _user_prompt = prompt

def get_and_clear_user_prompt():
    global _user_prompt
    with _user_prompt_lock:
        p = _user_prompt
        _user_prompt = None
        return p

def clear_user_prompt():
    global _user_prompt
    with _user_prompt_lock:
        _user_prompt = None

# ---- TTS cache directory ----
_tts_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "tts_cache",
)
os.makedirs(_tts_dir, exist_ok=True)

# Edge TTS 引擎复用（避免每句话冷启动）
_tts_engine = None
_tts_semaphore = asyncio.Semaphore(2)  # 限制并发 Edge TTS 请求数，避免被限流


def clear_tts_cache():
    """Clear TTS cache directory (called before each new conversation)."""
    try:
        for f in os.listdir(_tts_dir):
            fp = os.path.join(_tts_dir, f)
            if os.path.isfile(fp):
                os.remove(fp)
    except Exception:
        _log.debug("TTS cache clear failed", exc_info=True)


def _get_tts_engine():
    """根据 TTS_LLM_MODEL 创建/复用 TTS 引擎。

    返回 (engine, is_edge) — is_edge 表示是否是 Edge 引擎（需要信号量限流）。
    """
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine

    from config import api_config as _cfg
    model = (_cfg.TTS_LLM_MODEL or "").strip()

    # Edge TTS: provider_id 为 "edge"，model 为语音名
    if not model or _cfg.TTS_LLM_BASE_URL == "":
        # 无 API 配置 → 默认 Edge TTS
        from src.agent.tts_edge import EdgeTTSEngine
        voice = model if model in ("xiaoyi", "xiaoxiao", "yunxi", "yunjian", "yunxia", "yunyang") else "xiaoyi"
        _tts_engine = (EdgeTTSEngine(voice=voice, rate="-10%", cache_dir=_tts_dir), True)
    else:
        # 有 API 配置 → OpenAI 兼容 TTS
        from src.agent.tts_openai import OpenAITTSEngine
        tts_model = model if model else "tts-1"
        _tts_engine = (OpenAITTSEngine(
            api_key=_cfg.TTS_LLM_KEY, base_url=_cfg.TTS_LLM_BASE_URL,
            model=tts_model, voice="nova", cache_dir=_tts_dir), False)

    return _tts_engine


def _clear_tts_engine():
    """配置变更后清除缓存的引擎，下次调用重新创建。"""
    global _tts_engine
    _tts_engine = None


async def do_tts_sentence(sentence: str) -> dict | None:
    """Generate TTS for a single sentence.

    Returns {text, tts_url, duration_ms, phonemes} or None.
    """
    from config.settings import TTS_ENABLED
    if not TTS_ENABLED:
        return None

    if not sentence.strip():
        return None

    print(f"[TTS] request: {sentence[:50]}", file=sys.stderr, flush=True)

    _TTS_OK = re.compile(r'[一-鿿　-〿＀-￯a-zA-Z0-9\s.,;:!?。，；：！？～…—""《》【】「」『』\n·、\+\-*/=]')
    clean = ''.join(_TTS_OK.findall(sentence))
    clean = clean.replace('*', '')
    clean = re.sub(r'\s+', ' ', clean).strip()
    # 纯符号/标点跳过，不浪费 TTS 时间
    if not clean or not re.search(r'[一-鿿a-zA-Z0-9]', clean):
        print(f"[TTS] skip (no valid chars): {sentence[:50]}", file=sys.stderr, flush=True)
        return None

    try:
        engine, is_edge = _get_tts_engine()
        if is_edge:
            async with _tts_semaphore:
                result = await asyncio.wait_for(engine._synthesize(clean), timeout=30.0)
        else:
            result = await asyncio.wait_for(engine._synthesize(clean), timeout=30.0)
        if not result.audio_path or result.duration_ms <= 0:
            return None
        filename = os.path.basename(result.audio_path)

        phonemes = [
            {"t": p.text, "s": round(p.start_ms), "e": round(p.end_ms)}
            for p in result.phonemes
        ]
        try:
            from src.live2d.constants import char_vowel_class
            for ph in phonemes:
                ph["class"] = char_vowel_class(ph["t"])
        except Exception:
            _log.debug("vowel class failed", exc_info=True)

        return {
            "text": sentence,
            "tts_url": f"/tts-audio/{filename}",
            "duration_ms": round(result.duration_ms),
            "phonemes": phonemes,
        }
    except asyncio.TimeoutError:
        print(f"[TTS] synthesis timeout, skipping: {clean[:40]}", file=sys.stderr, flush=True)
        return None
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return None


def sseline(event: str, data: str) -> str:
    """Build a single SSE message line."""
    return f"event: {event}\ndata: {data}\n\n"


# ====================== 心跳程序 ======================
import time as _time_mod
import threading as _threading

_last_user_msg_time = _time_mod.time()
_heartbeat_interval = 120  # 初始间隔（秒）
_heartbeat_running = False

HEARTBEAT_PROMPT = """主人已经 {minutes} 分钟没说话了，你有点想主人了。
按你自己的性子，自然地表露一点想念，或主动做件小事：
- 蹭蹭屏幕、说句软乎乎的话
- 做个小动作卖卖萌（sleep / wink / cute_pose / tilt_head / shake）
- 想到主人可能累了，就提醒喝口水、休息一下
- 翻翻待办（read_todos）或回忆录，看看有什么想跟主人说的
- 上网找点有趣的话题，主动跟主人聊聊
- 或者什么都不做，继续安静陪着
像一只小猫突然想做点什么那样自然，别用「系统」「心跳」「用户没发消息」这些词。"""

# 心跳消息前缀标记：后端据此识别为 system 指令（而非用户输入）
HEARTBEAT_TAG = "[HEARTBEAT]"


def reload_heartbeat_interval():
    """设置变更时调用：立即更新心跳间隔"""
    global _heartbeat_interval
    try:
        from config.settings import HEARTBEAT_INTERVAL as _CFG
        _heartbeat_interval = _CFG
    except Exception:
        _heartbeat_interval = 120

def touch_heartbeat(from_heartbeat=False):
    """用户发消息时调用：重置计时器。心跳消息不重置间隔"""
    global _last_user_msg_time, _heartbeat_interval
    _last_user_msg_time = _time_mod.time()
    if not from_heartbeat:
        reload_heartbeat_interval()


def start_heartbeat():
    """启动心跳后台线程，idle 时 AI 自主响应（调用 generate_reply 走完整流程）"""
    global _heartbeat_running
    if _heartbeat_running:
        return
    _heartbeat_running = True

    def _loop():
        global _heartbeat_interval, _last_user_msg_time
        _time_mod.sleep(15)  # 等一切就绪
        while True:
            _time_mod.sleep(30)  # 每 30s 检查
            try:
                from config.settings import HEARTBEAT_ENABLED
                if not HEARTBEAT_ENABLED:
                    continue
                elapsed = _time_mod.time() - _last_user_msg_time
                if elapsed >= _heartbeat_interval:
                    minutes = max(1, int(elapsed / 60))
                    print(f"[heartbeat] 空闲{minutes}分钟 下次{_heartbeat_interval}s",
                          file=sys.stderr, flush=True)

                    # 让 AI 自主响应（后台生成回复）
                    _run_heartbeat_reply(minutes)

                    # 每次触发追加初始间隔（以初始值为步长线性增长）
                    try:
                        from config.settings import HEARTBEAT_INTERVAL as _BASE
                    except Exception:
                        _BASE = 120
                    _heartbeat_interval = min(_heartbeat_interval + _BASE, 3600)
                    _last_user_msg_time = _time_mod.time()
            except Exception as e:
                print(f"[heartbeat] 异常: {e}", file=sys.stderr, flush=True)

    _threading.Thread(target=_loop, daemon=True).start()


_heartbeat_pending = False
_heartbeat_msg = ""

def _run_heartbeat_reply(minutes: int):
    """心跳触发：标记待发送，前端轮询到后走正常 SSE 流程"""
    global _heartbeat_pending, _heartbeat_msg
    prompt = HEARTBEAT_PROMPT.format(minutes=minutes)
    _heartbeat_msg = HEARTBEAT_TAG + prompt
    _heartbeat_pending = True
    print(f"[heartbeat] 已标记待发送", file=sys.stderr, flush=True)


def check_and_clear_heartbeat() -> dict:
    """前端轮询：检查是否有待发送的心跳消息"""
    global _heartbeat_pending, _heartbeat_msg
    if _heartbeat_pending:
        msg = _heartbeat_msg
        _heartbeat_pending = False
        _heartbeat_msg = ""
        return {"pending": True, "message": msg}
    return {"pending": False}


# ---- Shared pet state (polled by Electron) ----
_pet_state = {
    "emotion": "neutral",
    "intensity": 0.3,
    "speaking": False,
    "speak_duration": 0,
    "_speak_id": 0,
    "tts_audio_url": "",
    "phonemes": [],
    "_pending_motion": "",
}


def get_pet_state() -> dict:
    return _pet_state


def pet_notify_speak(duration_ms: int, audio_url: str = "", phonemes: list = None):
    """Notify desktop: play audio + lip-sync."""
    _pet_state["speaking"] = True
    _pet_state["speak_duration"] = duration_ms
    _pet_state["_speak_id"] = int(time.time() * 1000)
    _pet_state["tts_audio_url"] = audio_url
    _pet_state["phonemes"] = phonemes or []
