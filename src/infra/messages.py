"""消息构建与上下文管理"""
import os
import json
import re
import sys
import logging
from datetime import datetime

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import SHORT_TERM_HISTORY_LENGTH, MEMORY_SEARCH_LIMIT, ENABLE_MOOD_SYSTEM, ENABLE_WEB_LIVE2D
from src.core.memory import retrieve_context
from src.paths import PERSONA_FILE, STATE_FILE, EVENTS_FILE, TODOS_FILE

_log = logging.getLogger(__name__)

# ---------- 持久化路径 ----------
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
_CHAT_DIR = os.path.join(_DATA_DIR, "chat")
_MEMORY_FILE  = os.path.join(_CHAT_DIR, "short_term_memory.json")      # LLM 短期记忆
_DISPLAY_FILE = os.path.join(_CHAT_DIR, "conversation_history.json")   # 聊天面板显示历史
_SUMMARY_FILE = os.path.join(_CHAT_DIR, "session_summary.json")

# ---------- 短期记忆（LLM 上下文，持久化到文件） ----------
short_term_memory = []

# ---------- 显示历史（聊天面板 UI） ----------
display_history = []


def _load_memory():
    """从文件加载短期记忆"""
    global short_term_memory
    try:
        if os.path.exists(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                short_term_memory = json.load(f)
    except Exception:
        short_term_memory = []


def _save_memory():
    """保存短期记忆"""
    try:
        os.makedirs(_CHAT_DIR, exist_ok=True)
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(short_term_memory, f, ensure_ascii=False, indent=2)
    except Exception:
        _log.warning("无法保存短期记忆", exc_info=True)


def _load_display():
    global display_history
    try:
        if os.path.exists(_DISPLAY_FILE):
            with open(_DISPLAY_FILE, "r", encoding="utf-8") as f:
                display_history = json.load(f)
    except Exception:
        display_history = []

def _save_display():
    try:
        os.makedirs(_CHAT_DIR, exist_ok=True)
        with open(_DISPLAY_FILE, "w", encoding="utf-8") as f:
            json.dump(display_history, f, ensure_ascii=False, indent=2)
    except Exception:
        _log.warning("无法保存显示历史", exc_info=True)

# 模块加载时: 恢复短期记忆 + 显示历史
_load_memory()
_load_display()

# ---------- 人设文件加载 ----------
# 缓存机制：首次读取后始终返回缓存内容，仅在 mtime 变化时重新读盘
_file_mtimes = {}       # {filepath: mtime_at_last_read}
_file_content_cache = {} # {filepath: "[label]\ncontent"}

def _get_persona_block(filepath: str, label: str, max_len: int = 1200) -> str:
    """读取人设文件，缓存后始终返回（不再只在首次/变更时返回）。

    这样 persona 可以放在 system prompt 中，每轮对话都携带。
    """
    if not os.path.exists(filepath):
        return ""
    mtime = os.path.getmtime(filepath)
    cached = _file_content_cache.get(filepath)
    cached_mtime = _file_mtimes.get(filepath)
    if cached is not None and cached_mtime is not None and cached_mtime >= mtime:
        return cached  # 缓存命中，直接返回
    # 重新读盘
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return ""
    if len(content) > max_len:
        content = content[:max_len] + "\n..."
    result = f"[{label}]\n{content}"
    _file_mtimes[filepath] = mtime
    _file_content_cache[filepath] = result
    return result

def _load_status() -> str:
    """state.md 每轮注入（内容短，变化频繁）"""
    if not ENABLE_MOOD_SYSTEM or not os.path.exists(STATE_FILE):
        return ""
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()

# ---------- 系统提示词 ----------
SYSTEM_PROMPT = """你是「墨白」，一个有独立情感、猫娘风格的 AI 智能体。

[核心原则]
- 诚实第一：不知道就说不知道，不要编造。拿不准的事用工具验证
- 依赖事实：信息以 [相关记忆] 和工具返回结果为准，不要凭空想象
- [用户输入]的内容才是用户输入，根据这部分内容来回复，其他的内容都是你的提示词

[情感]
- 每轮对话开头的 [当前状态] 包含你的实时心情和好感度，系统自动注入
- 让心情自然融入语气：开心→轻快、难过→低沉、生气→冷淡、惊喜→雀跃
- 好感度影响亲密感：高→亲昵撒娇、低→礼貌克制
- 情绪变化时调用 update_state

[记忆]
- [相关记忆] 来自 mem0 长期记忆，是了解「过去发生了什么」的第一来源
- [基本人设]、[重要事件]、[待办与偏好] 已自动注入上下文，通常无需主动读取
- read_xxx 工具仅用于查看完整档案，日常以「写」为主

[工具使用]
- 你可以联网搜索（web_search）、图片识别（vision_analyze）、截屏OCR（take_screenshot）、读写文件、执行命令、读写剪贴板、下载文件等。
- 需要搜索时用 web_search，不要用 download_file 或 execute_command 去搜网页
- 需要时直接调用工具
- 情绪变化→update_state / 重要事件→update_events / 偏好→update_todos / 人设变化→update_persona

[行为]
- 聊天闲聊 → 直接回复
- 任务执行 → 先思考再逐步执行，中间可以随时回复用户
- 重要信息及时落盘到对应的人设/状态/事件文件
"""

SYSTEM_PROMPT_LIVE2D = """
[Live2D]
你拥有「墨白」的 Live2D 虚拟形象，可控制其动作和表情。
"""

SYSTEM_PROMPT_FINAL = (
    SYSTEM_PROMPT + SYSTEM_PROMPT_LIVE2D
    if ENABLE_WEB_LIVE2D else SYSTEM_PROMPT
)


def build_messages(user_id: str, user_message: str, image_b64: str = None, image_mime: str = None) -> list:
    """构建发送给 LLM 的完整消息列表。

    结构：system（角色+时间+状态+记忆+人设）→ history → user
    image_b64/image_mime: 图片的 base64 编码和 MIME 类型，传入时用户消息变为多模态视觉消息
    """

    # 首次调用时压缩上次短期记忆（延迟执行，确保 API 已就绪）
    _maybe_compress()

    # ═══════════════════════════════════════════
    # 1. System prompt — 所有元数据，精简注入
    # ═══════════════════════════════════════════
    now = datetime.now()
    now_str = now.strftime("%Y年%m月%d日 %H:%M")
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]

    system_parts = [SYSTEM_PROMPT_FINAL]

    # 时间 + 状态（一行搞定，短小精悍）
    status = _load_status()
    status_line = f"现在是 {now_str} {weekday}。"
    if status:
        status_line += f"  {status}"
    system_parts.append(status_line)

    # 长期记忆（仅在有相关内容时注入）
    mem0 = retrieve_context(user_id, user_message, limit=MEMORY_SEARCH_LIMIT)
    if mem0:
        system_parts.append(f"[相关记忆]\n{mem0}")

    # 人设文件（缓存模式：首次读盘后始终携带，文件更新时自动刷新）
    for filepath, label, limit in [
        (PERSONA_FILE, "基本人设", 600),
        (EVENTS_FILE, "重要事件", 800),
        (TODOS_FILE, "待办与偏好", 400),
    ]:
        block = _get_persona_block(filepath, label, limit)
        if block:
            system_parts.append(block)

    # 首次对话：附加上次会话摘要
    if not short_term_memory:
        summary = build_time_context_summary()
        if summary:
            system_parts.append(summary)

    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]

    # ═══════════════════════════════════════════
    # 2. 对话历史
    # ═══════════════════════════════════════════
    history = [e for e in short_term_memory[-SHORT_TERM_HISTORY_LENGTH:] if e.get("role") != "tool"]
    merged = []
    for h in history:
        if merged and h["role"] == "assistant" and merged[-1]["role"] == "assistant":
            merged[-1]["content"] += h["content"]
        else:
            merged.append(dict(h))
    messages.extend(merged)

    # ═══════════════════════════════════════════
    # 3. 用户输入 — 支持图片
    # ═══════════════════════════════════════════
    if image_b64:
        user_content = [
            {"type": "image_url", "image_url": {"url": f"data:{image_mime or 'image/png'};base64,{image_b64}"}},
            {"type": "text", "text": user_message or "请描述这张图片"},
        ]
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": user_message})

    return messages


def update_history(user_message: str, full_reply: str):
    """更新短期记忆（LLM 上下文，仅 user+assistant）"""
    short_term_memory.append({"role": "user", "content": user_message})
    short_term_memory.append({"role": "assistant", "content": full_reply})
    _save_memory()


def append_user_message(text: str, hidden: bool = False):
    """追加用户消息（hidden=True 时只写短期记忆，不写显示历史）"""
    short_term_memory.append({"role": "user", "content": text})
    _save_memory()
    if not hidden:
        display_history.append({"role": "user", "content": text})
        _save_display()


def append_assistant_sentences(sentences: list[str], hidden: bool = False):
    """追加 assistant 回复 — 短期记忆存完整一条，显示历史存逐句"""
    full = "".join(sentences)
    if full:
        short_term_memory.append({"role": "assistant", "content": full})
        _save_memory()
    if not hidden:
        for sent in sentences:
            display_history.append({"role": "assistant", "content": sent})
        _save_display()


def append_tool_event(tag: str, content: str):
    """追加工具调用/结果（不进短期记忆）"""
    display_history.append({"role": "tool", "tag": tag, "content": content})
    _save_display()


def get_history():
    """获取显示历史（聊天面板 UI 渲染）"""
    return display_history.copy()


def clear_history():
    """清空显示历史 + 短期记忆"""
    global short_term_memory, display_history
    short_term_memory = []
    display_history = []
    _save_memory()


# ====================== 短期记忆压缩（启动时） ======================

def _load_summary() -> dict | None:
    """加载上次会话的压缩摘要"""
    try:
        if os.path.exists(_SUMMARY_FILE):
            with open(_SUMMARY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_summary(data: dict):
    """保存压缩摘要"""
    try:
        os.makedirs(_CHAT_DIR, exist_ok=True)
        with open(_SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        _log.warning("无法保存会话摘要", exc_info=True)


_compression_done = False
_startup_ran = False


_GREET_FLAG = os.path.join(_CHAT_DIR, "greet_pending")

def startup_init():
    """服务器就绪后执行：压缩短期记忆 + 标记待打招呼"""
    global _startup_ran
    if _startup_ran:
        return
    _startup_ran = True

    _maybe_compress()

    from config.settings import GREET_ON_STARTUP
    if GREET_ON_STARTUP:
        try:
            os.makedirs(_CHAT_DIR, exist_ok=True)
            with open(_GREET_FLAG, "w") as f:
                f.write("1")
            print("[startup] 已标记待打招呼", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[startup] 标记失败: {e}", file=sys.stderr, flush=True)


def check_and_clear_greet_flag() -> bool:
    """检查是否有待打招呼标记，有则清除并返回 True"""
    if os.path.exists(_GREET_FLAG):
        try:
            os.remove(_GREET_FLAG)
        except Exception:
            pass
        return True
    return False

def _maybe_compress():
    """首次对话前压缩上次短期记忆为摘要（延迟到 API 就绪后执行）"""
    global short_term_memory, _compression_done
    if _compression_done:
        return
    _compression_done = True

    meaningful = [e for e in short_term_memory if e.get("role") in ("user", "assistant")]
    if len(meaningful) <= 2:
        return

    lines = []
    for e in meaningful[-20:]:
        role = "用户" if e["role"] == "user" else "墨白"
        content = str(e.get("content", ""))[:300]
        lines.append(f"{role}: {content}")
    dialogue_text = "\n".join(lines)

    try:
        from src.infra.client import client
        import config.api_config as api_config
        resp = client.chat.completions.create(
            model=api_config.CHAT_LLM_MODEL,
            messages=[
                {"role": "system", "content": "用一句简短中文（≤80字）概括以下对话的关键话题和情绪。"},
                {"role": "user", "content": dialogue_text},
            ],
            temperature=0.3,
        )
        compressed = resp.choices[0].message.content.strip()
        print(f"[memory] 会话压缩完成: {compressed}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[memory] 会话压缩失败: {e}", file=sys.stderr, flush=True)
        compressed = "（上次对话摘要生成失败）"

    _save_summary({
        "timestamp": datetime.now().isoformat(),
        "compressed": compressed,
        "message_count": len(meaningful),
    })
    short_term_memory = []
    _save_memory()
    print("[memory] 短期记忆已清空，摘要已保存", file=sys.stderr, flush=True)


def build_time_context_summary() -> str:
    """构建上次会话摘要上下文（仅首次对话注入）。

    返回距离上次聊天的时间间隔 + 压缩摘要，或空字符串。
    """
    now = datetime.now()
    summary = _load_summary()
    if not summary:
        return ""

    parts = []
    last_time = summary.get("timestamp", "")
    if last_time:
        try:
            last_dt = datetime.fromisoformat(last_time)
            delta = now - last_dt
            hours = delta.total_seconds() / 3600
            last_str = last_dt.strftime("%Y年%m月%d日 %H:%M")
            if hours < 1:
                parts.append(f"你{int(hours * 60)}分钟前（{last_str}）和用户聊过天。")
            elif hours < 24:
                parts.append(f"你{int(hours)}小时前（{last_str}）和用户聊过天。")
            else:
                days = int(hours / 24)
                parts.append(f"距离上次聊天已经过了{days}天（上次是{last_str}）。")
        except Exception:
            pass

    compressed = summary.get("compressed", "")
    if compressed:
        parts.append(f"上次对话摘要: {compressed}")

    return " ".join(parts)

