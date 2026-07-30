"""项目路径管理 - 所有路径集中定义"""
import os
import shutil
import sys

# ---------- 项目根目录 ----------
# paths.py 位于 src/ 下，向上两级到项目根目录
_CURRENT_FILE = os.path.abspath(__file__)
SRC_DIR = os.path.dirname(_CURRENT_FILE)
PROJECT_ROOT = os.path.dirname(SRC_DIR)  

# ---------- 常用子目录 ----------
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
INFRA_DIR = os.path.join(SRC_DIR, "infra")
CORE_DIR = os.path.join(SRC_DIR, "core")
TOOLS_DIR = os.path.join(SRC_DIR, "tools")
UI_DIR = os.path.join(SRC_DIR, "ui")
UTILS_DIR = os.path.join(SRC_DIR, "utils")

# ---------- 数据子目录 ----------
HF_CACHE_DIR = os.path.join(DATA_DIR, "hf_cache")
MEM0_DB_DIR = os.path.join(DATA_DIR, "mem0_db")
WORKSPACE_DIR = os.path.join(DATA_DIR, "Workspace")

# ---------- 人设目录 ----------
PERSONA_DIR = os.path.join(DATA_DIR, "persona")
PERSONA_FILE = os.path.join(PERSONA_DIR, "persona.md")
STATE_FILE = os.path.join(PERSONA_DIR, "state.md")
EVENTS_FILE = os.path.join(PERSONA_DIR, "events.md")
TODOS_FILE = os.path.join(PERSONA_DIR, "todos.md")
_PERSONA_EXAMPLE = os.path.join(PERSONA_DIR, "persona.example.md")

if not os.path.exists(PERSONA_FILE):
    if os.path.exists(_PERSONA_EXAMPLE):
        shutil.copy2(_PERSONA_EXAMPLE, PERSONA_FILE)
    else:
        with open(PERSONA_FILE, "w", encoding="utf-8") as f:
            f.write("# 墨白 — AI 助手人设\n\n"
                    "## 基本设定\n- 名字: 墨白\n- 性格: 可爱，幽默，跳脱，萌\n"
                    "- 语言: 中文为主，语气自然简洁，偶尔幽默\n\n"
                    "## 用户画像\n- 称呼: 主人\n")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write("心情: 平静\n好感度: 50/100\n")
        with open(EVENTS_FILE, "w", encoding="utf-8") as f:
            f.write("# 重要事件\n\n")
        with open(TODOS_FILE, "w", encoding="utf-8") as f:
            f.write("# 待办与偏好\n\n")
    print("[Mcode] 已创建默认 persona.md 和 state.md，可在设置界面自定义人设",
          file=sys.stderr, flush=True)

# ---------- MCP 服务器路径 ----------
MCP_SERVER_PATH = os.path.join(INFRA_DIR, "mcp_server.py")

# ---------- 确保数据目录存在 ----------
os.makedirs(HF_CACHE_DIR, exist_ok=True)
os.makedirs(MEM0_DB_DIR, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)