"""Mem0 系统配置 - 引用 api_config 中的配置"""
import os
import config.api_config as api_config
from src.utils.paths import HF_CACHE_DIR, MEM0_DB_DIR, WORKSPACE_DIR

# ========== 环境变量（一次性设置） ==========
os.environ["HF_HOME"] = HF_CACHE_DIR
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["MEM0_TELEMETRY"] = "false"
os.environ["MEM0_CLIENT_TELEMETRY"] = "false"
os.environ["POSTHOG_DISABLED"] = "1"
os.environ["POSTHOG_HOST"] = "http://127.0.0.1:1"  # 本地黑洞
os.environ["POSTHOG_API_KEY"] = ""                 # 清空 API Key 彻底禁用

# 供 Mem0 内部 LLM（记忆提取）使用
os.environ["OPENAI_API_KEY"] = api_config.MEMORY_LLM_KEY
os.environ["OPENAI_BASE_URL"] = api_config.MEMORY_LLM_BASE_URL

# ========== Mem0 配置字典 ==========
_MEMORY_EXTRA_HEADERS = api_config.opencode_headers(api_config.MEMORY_LLM_BASE_URL)
MEM0_CONFIG = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": api_config.MEMORY_LLM_MODEL,
            "api_key": api_config.MEMORY_LLM_KEY,
            "openai_base_url": api_config.MEMORY_LLM_BASE_URL,
            # OpenCode 网关要求附加 x-opencode-session 头（非 opencode 时为空 dict）
            **({"default_headers": _MEMORY_EXTRA_HEADERS} if _MEMORY_EXTRA_HEADERS else {}),
        },
    },
    "embedder": {
        "provider": "huggingface",
        "config": {"model": "all-MiniLM-L6-v2"},
    },
    "vector_store": {
        "provider": "chroma",
        "config": {
            "collection_name": "mem0_memories",
            "path": MEM0_DB_DIR,
        },
    },
    "disable_nlp_processing": True,
    "telemetry": {"enabled": False},
}

# ========== 对话参数 ==========
MEMORY_SEARCH_LIMIT = 3           # 检索记忆条数
SHORT_TERM_HISTORY_LENGTH = 5     # 保留最近 N 轮短期对话

# ========== MCP 工作目录配置 ==========
# 设置为 None 表示允许访问整个文件系统
# 设置为具体路径则限制在指定目录内
MCP_WORKSPACE_DIR = WORKSPACE_DIR  # 默认 Workspace


# ========== 工具调用配置 ==========
ENABLE_TOOLS = True  # 是否启用工具调用
TOOL_CHOICE = "auto"  # "auto", "none", "required"

# ========== ReAct Agent 配置 ==========
# 开启后 LLM 自主规划并逐步执行复杂任务（思考→执行→观察→循环）
ENABLE_AGENT_MODE = True
# Agent 最大执行步数（防止死循环）
MAX_AGENT_STEPS = 10

# ========== 心情好感度系统 ==========
# AI 会根据对话自动调整 persona.md 中的心情值和好感度
ENABLE_MOOD_SYSTEM = True

# ========== Web Live2D 渲染 ==========
# Electron 内嵌 Live2D 渲染是否启用
ENABLE_WEB_LIVE2D = True
TOOL_TTS_ENABLED = True  # 工具调用时是否播报语音
TTS_ENABLED = True       # 全局 TTS 开关（关闭后只用文字+口型）

# ========== 启动行为 ==========
GREET_ON_STARTUP = True  # 启动后 Agent 主动打招呼
HEARTBEAT_INTERVAL = 120  # 心跳间隔（秒）
HEARTBEAT_ENABLED = True  # 是否启用心跳

# ========== 桌面控制 ==========
# 允许 AI 通过鼠标键盘工具（mouse_click/keyboard_type 等）操作电脑。
# 工具子进程每次调用时实时读取 app_config.json 中的 desktop_control_enabled
DESKTOP_CONTROL_ENABLED = True


# ========== 从 app_config.json 加载运行时配置 ==========
def reload_from_dict(cfg: dict):
    """Hot-reload runtime config values from a dict (called by API route on save)."""
    global SHORT_TERM_HISTORY_LENGTH, MEMORY_SEARCH_LIMIT, MAX_AGENT_STEPS, TOOL_CHOICE, GREET_ON_STARTUP, HEARTBEAT_INTERVAL, HEARTBEAT_ENABLED, TOOL_TTS_ENABLED, TTS_ENABLED, DESKTOP_CONTROL_ENABLED
    if "short_term_history_length" in cfg:
        SHORT_TERM_HISTORY_LENGTH = int(cfg["short_term_history_length"])
    if "memory_search_limit" in cfg:
        MEMORY_SEARCH_LIMIT = int(cfg["memory_search_limit"])
    if "max_agent_steps" in cfg:
        MAX_AGENT_STEPS = int(cfg["max_agent_steps"])
    if "tool_choice" in cfg:
        TOOL_CHOICE = str(cfg["tool_choice"])
    if "greet_on_startup" in cfg:
        GREET_ON_STARTUP = bool(cfg["greet_on_startup"])
    if "heartbeat_interval" in cfg:
        HEARTBEAT_INTERVAL = int(cfg["heartbeat_interval"])
    if "heartbeat_enabled" in cfg:
        HEARTBEAT_ENABLED = bool(cfg["heartbeat_enabled"])
    if "heartbeat_interval" in cfg or "heartbeat_enabled" in cfg:
        try:
            from src.server.routes.shared import reload_heartbeat_interval
            reload_heartbeat_interval()
        except Exception:
            pass
    if "tool_tts_enabled" in cfg:
        TOOL_TTS_ENABLED = bool(cfg["tool_tts_enabled"])
    if "tts_enabled" in cfg:
        TTS_ENABLED = bool(cfg["tts_enabled"])
    if "desktop_control_enabled" in cfg:
        DESKTOP_CONTROL_ENABLED = bool(cfg["desktop_control_enabled"])


def _load_app_config():
    """Load runtime config from app_config.json on module init."""
    import json as _json
    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_config.json")
    try:
        if os.path.exists(_cfg_path):
            with open(_cfg_path, "r", encoding="utf-8") as f:
                reload_from_dict(_json.load(f))
    except Exception:
        pass


_load_app_config()