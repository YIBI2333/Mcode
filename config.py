"""Mem0 系统配置 - 引用 api_config 中的配置"""
import os
import api_config

# ========== 项目根目录 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ========== 模型缓存与数据目录（在项目内） ==========
HF_CACHE_DIR = os.path.join(BASE_DIR, "hf_cache")
MEMORY_DB_DIR = os.path.join(BASE_DIR, "mem0_db")

# ========== 环境变量（一次性设置） ==========
os.environ["HF_HOME"] = HF_CACHE_DIR
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["MEM0_TELEMETRY"] = "false"
os.environ["MEM0_CLIENT_TELEMETRY"] = "false"

# 供 Mem0 内部 LLM（记忆提取）使用
os.environ["OPENAI_API_KEY"] = api_config.MEMORY_LLM_KEY
os.environ["OPENAI_BASE_URL"] = api_config.MEMORY_LLM_BASE_URL

# ========== Mem0 配置字典 ==========
MEM0_CONFIG = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": api_config.MEMORY_LLM_MODEL,
            "api_key": api_config.MEMORY_LLM_KEY,
            "openai_base_url": api_config.MEMORY_LLM_BASE_URL,
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
            "path": MEMORY_DB_DIR,
        },
    },
    "disable_nlp_processing": True,
    "telemetry": {"enabled": False},
}

# ========== 对话参数 ==========
MEMORY_SEARCH_LIMIT = 2           # 检索记忆条数
SHORT_TERM_HISTORY_LENGTH = 4     # 保留最近 N 轮短期对话





# ========== MCP 工作目录配置 ==========
# 设置为 None 表示允许访问整个文件系统
# 设置为具体路径则限制在指定目录内
MCP_WORKSPACE_DIR = os.path.join(BASE_DIR, "Workspace")  # 默认 Workspace


# ========== 工具调用配置 ==========
ENABLE_TOOLS = True  # 是否启用工具调用
TOOL_CHOICE = "auto"  # "auto", "none", "required"