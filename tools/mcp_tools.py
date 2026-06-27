"""MCP 工具适配器 - 将 MCP 工具转换为 OpenAI function calling 格式"""
import json
from mcp_client import mcp_client

# 连接 MCP 服务器（启动时执行一次）
def init_mcp():
    """初始化 MCP 客户端（在程序启动时调用）"""
    try:
        mcp_client.connect()
        print("MCP 客户端连接成功。")
    except Exception as e:
        print(f"MCP 连接失败：{e}")

def get_tools_schema():
    tools = mcp_client.list_tools()
    schema = []
    for tool in tools:
        # 只保留核心字段，确保格式兼容
        params = {
            "type": "object",
            "properties": tool.inputSchema.get("properties", {}),
            "required": tool.inputSchema.get("required", [])
        }
        schema.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": params
            }
        })
    return schema
def execute_mcp_tool(tool_call):
    """执行 MCP 工具调用，返回结果字符串"""
    func_name = tool_call.function.name
    func_args = json.loads(tool_call.function.arguments)
    return mcp_client.call_tool(func_name, func_args)