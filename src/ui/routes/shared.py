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

def reset_cancel():
    """Reset cancel flag before a new generation starts."""
    with _cancel_lock:
        _cancel_event.clear()

def is_cancelled() -> bool:
    """Check if cancellation has been requested (thread-safe)."""
    return _cancel_event.is_set()

# ---- TTS cache directory ----
_tts_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "tts_cache",
)
os.makedirs(_tts_dir, exist_ok=True)

# Edge TTS 引擎复用（避免每句话冷启动）
_tts_engine = None


def clear_tts_cache():
    """Clear TTS cache directory (called before each new conversation)."""
    try:
        for f in os.listdir(_tts_dir):
            fp = os.path.join(_tts_dir, f)
            if os.path.isfile(fp):
                os.remove(fp)
    except Exception:
        _log.debug("TTS cache clear failed", exc_info=True)


async def do_tts_sentence(sentence: str) -> dict | None:
    """Generate TTS for a single sentence.

    Returns {text, tts_url, duration_ms, phonemes} or None.
    """
    global _tts_engine
    if not sentence.strip():
        return None

    _TTS_OK = re.compile(r'[一-鿿　-〿＀-￯a-zA-Z0-9\s.,;:!?。，；：！？～…—""《》【】「」『』\n·、\+\-*/=]')
    clean = ''.join(_TTS_OK.findall(sentence))
    clean = clean.replace('*', '')
    clean = re.sub(r'\s+', ' ', clean).strip()
    # 纯符号/标点跳过，不浪费 TTS 时间
    if not clean or not re.search(r'[一-鿿a-zA-Z0-9]', clean):
        return None

    try:
        from src.tts.edge_tts import EdgeTTSEngine
        if _tts_engine is None:
            _tts_engine = EdgeTTSEngine(voice="xiaoyi", rate="-10%", cache_dir=_tts_dir)
        result = await asyncio.wait_for(_tts_engine._synthesize(clean), timeout=30)
        if not result.audio_path or result.duration_ms <= 0:
            return None
        filename = os.path.basename(result.audio_path)

        phonemes = [
            {"t": p.text, "s": round(p.start_ms), "e": round(p.end_ms)}
            for p in result.phonemes
        ]
        try:
            from src.utils.live2d_constants import char_vowel_class
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

HEARTBEAT_PROMPT = """[系统心跳]
用户已经 {minutes} 分钟没有发消息了。
如果用户之前说正在做某件事，可以继续陪用户聊这个话题。
或者，你现在也可以自主决定做点什么（不要做危险或破坏性操作）。比如：

- 什么也不做，安静等待
- 做一些 live2d_motion 动作卖卖萌（sleep / wink / cute_pose / tilt_head / shake 等）
- 整理一下人设档案（看看 persona / events / todos 有没有需要更新的）
- 看看待办清单（read_todos）
- 回顾最近的对话，思考一下
- 截屏看看主人在做什么（take_screenshot）
- 看看工作区有些什么
- 自己上网搜索一些有趣的话题
- 规划一下未来，和自己的想法
- 进行一些头脑风暴和思考训练

请简短自然地回应。"""


def touch_heartbeat(from_heartbeat=False):
    """用户发消息时调用：重置计时器。心跳消息不重置间隔"""
    global _last_user_msg_time, _heartbeat_interval
    _last_user_msg_time = _time_mod.time()
    if not from_heartbeat:
        try:
            from config.settings import HEARTBEAT_INTERVAL as _CFG
            _heartbeat_interval = _CFG
        except Exception:
            _heartbeat_interval = 120


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
    _heartbeat_msg = prompt
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
