"""MCP 工具适配器"""
import json
import logging
from collections import namedtuple
from src.infra.mcp_client import mcp_client

_log = logging.getLogger(__name__)

# 内部工具，不暴露给 LLM
_AUTO_TOOLS = set()

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
    schema = []
    seen = set()
    for tool in tools:
        if tool.name in _AUTO_TOOLS or tool.name in seen:
            continue
        seen.add(tool.name)
        params = {
            "type": "object",
            "properties": tool.inputSchema.get("properties", {}),
            "required": tool.inputSchema.get("required", [])
        }
        schema.append({
            "type": "function",
            "function": {"name": tool.name, "description": tool.description, "parameters": params}
        })
    return schema


def execute_mcp_tool(tool_call):
    func_name = tool_call.function.name
    func_args = json.loads(tool_call.function.arguments)
    return mcp_client.call_tool(func_name, func_args)
