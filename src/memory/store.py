"""短期记忆 + 显示历史 — 持久化 I/O + 更新 API"""
import os, json, logging

_log = logging.getLogger(__name__)

# ═══════════════════ 路径 ═══════════════════
_DATA_DIR    = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
_CHAT_DIR    = os.path.join(_DATA_DIR, "chat")
_MEMORY_FILE = os.path.join(_CHAT_DIR, "short_term_memory.json")
_DISPLAY_FILE = os.path.join(_CHAT_DIR, "conversation_history.json")

# ═══════════════════ 全局状态 ═══════════════════
short_term_memory = []    # LLM 上下文
display_history = []      # 聊天面板 UI

# ═══════════════════ I/O ═══════════════════
def _load_json(path, default=None):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception: pass
    return default if default is not None else []

def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        _log.warning(f"保存失败: {path}", exc_info=True)

def _load_memory():
    global short_term_memory
    short_term_memory = _load_json(_MEMORY_FILE, [])

def _save_memory():
    _save_json(_MEMORY_FILE, short_term_memory)

def _load_display():
    global display_history
    display_history = _load_json(_DISPLAY_FILE, [])

def _save_display():
    _save_json(_DISPLAY_FILE, display_history)

_load_memory()
_load_display()

# ═══════════════════ 更新 API ═══════════════════
def append_user_message(text: str, hidden: bool = False):
    if hidden:
        # 定时任务/打招呼/心跳等系统触发消息：不写入短期记忆。
        # AI 对这类触发的回复（assistant）已由 append_assistant_sentences 记录，
        # AI 从自己的回复即可记得"主动提醒过喝水/打过招呼"，无需存系统引导原文。
        return
    short_term_memory.append({"role": "user", "content": text})
    _save_memory()
    display_history.append({"role": "user", "content": text})
    _save_display()

def append_assistant_sentences(sentences: list[str], hidden: bool = False):
    full = "".join(sentences)
    if full:
        short_term_memory.append({"role": "assistant", "content": full})
        _save_memory()
    if not hidden:
        for s in sentences:
            display_history.append({"role": "assistant", "content": s})
        _save_display()
    from src.memory.compress import _schedule_compress
    _schedule_compress()


def append_tool_event(tag: str, content: str):
    display_history.append({"role": "tool", "tag": tag, "content": content})
    _save_display()

def append_status_event(type_: str, content: str, meta: dict = None):
    """落盘任务状态事件（task-start / plan / task-end），供历史回放还原。"""
    entry = {"role": "status", "type": type_, "content": content}
    if meta:
        entry["meta"] = meta
    display_history.append(entry)
    _save_display()

def update_history(user_message: str, full_reply: str):
    short_term_memory.append({"role": "user", "content": user_message})
    short_term_memory.append({"role": "assistant", "content": full_reply})
    _save_memory()

def get_history():
    return display_history.copy()

def clear_history():
    global short_term_memory, display_history
    short_term_memory = []
    display_history = []
    _save_memory()
    _save_display()
