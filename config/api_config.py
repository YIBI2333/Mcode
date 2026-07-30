"""API 凭据配置 — 从 model_config.json 加载"""

import os
import json
import shutil
import sys

# ====================== 模块变量（由 _load_model_config 赋值） ======================

CHAT_LLM_KEY = ""
CHAT_LLM_MODEL = ""
CHAT_LLM_BASE_URL = ""

MEMORY_LLM_KEY = ""
MEMORY_LLM_MODEL = ""
MEMORY_LLM_BASE_URL = ""

TOOL_LLM_KEY = ""
TOOL_LLM_MODEL = ""
TOOL_LLM_BASE_URL = ""

VISION_LLM_KEY = ""
VISION_LLM_MODEL = ""
VISION_LLM_BASE_URL = ""


# ====================== 从 model_config.json 加载 ======================

_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_CONFIG_DIR, "model_config.json")
_EXAMPLE_PATH = os.path.join(_CONFIG_DIR, "model_config.example.json")


def _ensure_config_exists():
    """首次启动时从 template 自动创建 model_config.json"""
    if os.path.exists(_JSON_PATH):
        return

    if os.path.exists(_EXAMPLE_PATH):
        shutil.copy2(_EXAMPLE_PATH, _JSON_PATH)
        print(
            f"[Mcode] 已从 model_config.example.json 自动创建 model_config.json\n"
            f"        请编辑 {_JSON_PATH} 填入你的 API Key 后重新启动",
            file=sys.stderr, flush=True,
        )
    else:
        # example 也没有 → 创建最小骨架
        skeleton = {
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-your-key-here",
                    "is_default": False,
                },
            ],
            "roles": {
                "chat": {
                    "label": "对话生成",
                    "desc": "AI 对话回复使用的模型",
                    "mode": "single",
                    "entries": [
                        {"provider_ids": ["deepseek"], "model": "deepseek-chat", "enabled": True},
                    ],
                },
                "memory": {
                    "label": "记忆引擎",
                    "desc": "长期记忆提取和检索使用的模型",
                    "mode": "single",
                    "entries": [
                        {"provider_ids": ["deepseek"], "model": "deepseek-chat", "enabled": True},
                    ],
                },
                "tool": {
                    "label": "工具调用",
                    "desc": "工具选择与 Live2D 情绪分析使用的模型",
                    "mode": "single",
                    "entries": [
                        {"provider_ids": ["deepseek"], "model": "deepseek-chat", "enabled": True},
                    ],
                },
                "vision": {
                    "label": "图片识别",
                    "desc": "图片理解和视觉分析使用的模型",
                    "mode": "single",
                    "entries": [
                        {"provider_ids": ["deepseek"], "model": "minimax-m3", "enabled": True},
                    ],
                },
            },
        }
        with open(_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(skeleton, f, ensure_ascii=False, indent=2)
        print(
            f"[Mcode] 已创建默认 model_config.json\n"
            f"        请编辑 {_JSON_PATH} 填入你的 API Key 后重新启动",
            file=sys.stderr, flush=True,
        )


def _load_model_config():
    """从 JSON 配置文件读取模型分配，覆盖模块级变量"""
    try:
        with open(_JSON_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError:
        print(f"[Mcode] model_config.json 格式错误，将使用空配置", file=sys.stderr, flush=True)
        return
    except FileNotFoundError:
        print(f"[Mcode] model_config.json 不存在，将使用空配置", file=sys.stderr, flush=True)
        return

    providers = {}
    defaults = []
    for p in cfg.get("providers", []):
        providers[p["id"]] = p
        if p.get("is_default"):
            defaults.append(p)

    roles = cfg.get("roles", {})
    _apply_role(roles.get("chat"), providers, defaults, "CHAT")
    _apply_role(roles.get("memory"), providers, defaults, "MEMORY")
    _apply_role(roles.get("tool"), providers, defaults, "TOOL")
    _apply_role(roles.get("vision"), providers, defaults, "VISION")


def _apply_role(role_cfg, providers, defaults, prefix):
    """将角色配置应用到 CHAT_LLM_*/MEMORY_LLM_*/TOOL_LLM_* 模块变量"""
    if role_cfg:
        entries = role_cfg.get("entries", [])
        for entry in entries:
            if not entry.get("enabled"):
                continue
            pids = entry.get("provider_ids") or []
            if not pids:
                pid = entry.get("provider_id", "")
                if pid:
                    pids = [pid]
            if not pids:
                continue
            pid = pids[0]
            p = providers.get(pid, {})
            module = sys.modules[__name__]
            setattr(module, f"{prefix}_LLM_KEY", p.get("api_key", ""))
            setattr(module, f"{prefix}_LLM_MODEL", entry.get("model", ""))
            setattr(module, f"{prefix}_LLM_BASE_URL", p.get("base_url", ""))
            return

    # 无启用条目 → 回退到默认服务商
    if defaults:
        dp = defaults[0]
        module = sys.modules[__name__]
        setattr(module, f"{prefix}_LLM_KEY", dp.get("api_key", ""))
        setattr(module, f"{prefix}_LLM_MODEL", dp.get("default_model", "") or dp.get("name", ""))
        setattr(module, f"{prefix}_LLM_BASE_URL", dp.get("base_url", ""))
        return


_ensure_config_exists()
_load_model_config()
