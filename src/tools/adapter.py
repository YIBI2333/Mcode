"""MCP 工具适配器"""
import json
import logging
from collections import namedtuple
from src.tools.client import mcp_client

_log = logging.getLogger(__name__)

# 内部工具，不暴露给 LLM
_AUTO_TOOLS = set()

# ask_user 等待用户答复的超时（秒）。超时后自动继续，避免会话无限挂起。
USER_ASK_TIMEOUT = 240

_FakeTC = namedtuple("FakeTC", ["function"])
_FakeFunc = namedtuple("FakeFunc", ["name", "arguments"])


def init_mcp():
    try:
        mcp_client.connect()
        if mcp_client._connected:
            tools = mcp_client.list_tools()
            print(f"[MCP] 客户端连接成功，{len(tools)} 个工具可用。")
        else:
            print("[MCP] 工具服务器未启动（MCP 工具不可用）")
    except Exception as e:
        _log.warning("MCP 工具服务器连接失败", exc_info=True)


def get_tools_schema():
    try:
        tools = mcp_client.list_tools()
    except Exception:
        _log.debug("list_tools failed", exc_info=True)
        return []

    # 控制工具不来自 MCP 服务器，MCP 只提供业务工具
    from src.agent.control_tools import CONTROL_TOOL_SCHEMAS, CONTROL_TOOL_NAMES

    # 工作区提示（让 LLM 清楚可访问边界）
    try:
        from config.settings import MCP_WORKSPACE_DIR as _WS
    except Exception:
        _WS = None
    _PATH_TOOLS = {
        "list_directory", "read_text_file", "read_json_file", "read_yaml_file",
        "write_file", "delete_file", "move_file", "download_file",
        "vision_analyze", "take_screenshot", "execute_command",
    }
    _ws_hint = (
        f"\n[工作区] 当前可访问目录: {_WS}。相对路径都会解析到该目录下，"
        "访问工作区之外的文件会被拒绝。"
        if _WS else
        "\n[工作区] 无限制（可访问整个文件系统）。"
    )

    schema = []
    seen = set()
    for tool in tools:
        if tool.name in _AUTO_TOOLS or tool.name in seen:
            continue
        # 防止 MCP 端意外定义了控制工具名 → 跳过以本地控制工具为准
        if tool.name in CONTROL_TOOL_NAMES:
            continue
        seen.add(tool.name)
        params = {
            "type": "object",
            "properties": tool.inputSchema.get("properties", {}),
            "required": tool.inputSchema.get("required", [])
        }
        desc = tool.description
        if tool.name in _PATH_TOOLS:
            desc = (desc or "") + _ws_hint
        schema.append({
            "type": "function",
            "function": {"name": tool.name, "description": desc, "parameters": params}
        })
    schema.extend(CONTROL_TOOL_SCHEMAS)
    return schema


def execute_mcp_tool(tool_call):
    func_name = tool_call.function.name
    func_args = json.loads(tool_call.function.arguments)

    # 控制工具不进 MCP，由 workflow 层拦截调用（兜底防御）
    from src.agent.control_tools import is_control_tool
    if is_control_tool(func_name):
        return f"[控制工具 {func_name} 不应由 execute_mcp_tool 执行]"

    if func_name == "ask_user":
        return _handle_ask_user(func_args)

    return mcp_client.call_tool(func_name, func_args)


def _handle_ask_user(args: dict) -> str:
    """ask_user 工具 → 暂停等待用户输入"""
    question = args.get("question", "").strip()
    if not question:
        return "[用户确认] 已继续"

    from src.agent.session import get_or_create_session
    from src.agent.pipeline import UserPromptEvent
    import src.server.routes.shared as _shared

    session = get_or_create_session()
    # 通知 SSE 路由发送 user-prompt 给前端
    _shared.set_user_prompt(UserPromptEvent(
        question=question, session_id=session.session_id))
    # 阻塞等待用户回复（带超时；取消或超时都会解除阻塞，避免会话无限挂起）
    response = session.wait_for_user(question, timeout=USER_ASK_TIMEOUT)
    _shared.clear_user_prompt()
    if _shared.is_cancelled():
        return "[用户已取消]"
    if response is None:
        return "[用户未回复，已超时自动继续]"
    return f"[用户回复] {response}" if response.strip() else "[用户确认] 已继续"
