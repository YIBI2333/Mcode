"""工具名称 → 自然语言描述映射

供 chat.py 中 _step_desc() 使用，将所有 MCP 工具调用转为用户可读的自然语言描述。
"""
import json as _json
import logging

_log = logging.getLogger(__name__)

# 覆盖 mcp_server.py 中 TOOL_HANDLERS 全部业务工具 + Agent 控制工具
TOOL_DESC_MAP: dict[str, str] = {
    # 文件操作
    "list_directory": "让我看看都有什么文件...",
    "read_text_file": "我来读一下这个文件...",
    "read_json_file": "正在读取 JSON 文件...",
    "read_yaml_file": "正在读取 YAML 文件...",
    "write_file": "正在写入文件...",
    "delete_file": "正在删除文件...",
    "move_file": "正在移动文件...",

    # 系统
    "execute_command": "正在执行命令...",
    "get_current_time": "让我看看现在几点了...",
    "get_system_info": "正在查看系统信息...",
    "get_env_var": "正在读取环境变量...",

    # 网络
    "web_search": "让我在网上搜索一下...",
    "vision_analyze": "让我看看这张图片...",
    "download_file": "正在下载文件...",

    # 桌面交互
    "clipboard_read": "正在读取剪贴板...",
    "clipboard_write": "正在写入入剪贴板...",

    # Live2D
    "live2d_motion": "看我的~",

    # 屏幕
    "take_screenshot": "让我看看屏幕上有什么...",

    # Agent 元能力
    "think": "让我想一下...",
    "ask_user": "我需要确认一下...",

    # 人设档案
    "read_persona": "让我看看我的基本人设...",
    "update_persona": "记下来了，关于我的设定~",
    "read_state": "让我感受一下现在的心情...",
    "update_state": "心情更新了~",
    "read_events": "让我翻翻回忆录...",
    "update_events": "这件事要记下来！",
    "read_todos": "让我看看待办清单...",
    "update_todos": "待办已更新~",
    "add_scheduled_task": "正在设置定时任务...",
    "list_scheduled_tasks": "让我看看有哪些定时任务...",
    "delete_scheduled_task": "正在删除定时任务...",
    "image_set": "记下这张图片了~",
    "image_list": "让我看看有哪些图片...",
    "image_delete": "删掉这张图片~",

    # Agent 控制工具（无副作用，workflow 层拦截处理）
    "plan": "让我规划一下任务步骤...",
    "speak": "",
    "reflect": "让我反思一下进展...",
    "finish": "任务完成~",
}


def step_desc(tool_name: str, tool_args: str = "") -> str:
    """将工具调用转为一句自然语言描述，用于对话显示和 TTS 播报"""
    desc = TOOL_DESC_MAP.get(tool_name)
    if desc:
        return desc

    try:
        if tool_args:
            args = _json.loads(tool_args)
            for val in args.values():
                if isinstance(val, str) and len(val) < 40 and val.strip():
                    return f"正在处理「{val}」..."
    except Exception:
        _log.debug("step_desc JSON parse failed, using fallback", exc_info=True)

    return f"正在调用 {tool_name}..."
