"""MCP 客户端封装（同步适配器）"""
import asyncio
import os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sys

class MCPClient:
    def __init__(self, command: str, args: list = None):
        self.command = command
        self.args = args or []
        self.session = None
        self._stdio_ctx = None
        self._session_ctx = None
        self._read = None
        self._write = None
        self._loop = None

    def _run_async(self, coro):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop.run_until_complete(coro)

    def connect(self):
        """建立连接并初始化会话（同步）"""
        async def _connect():
            server_params = StdioServerParameters(
                command=self.command,
                args=self.args
            )
            # 进入上下文管理器，保存引用（不要用 async with，手动管理）
            self._stdio_ctx = stdio_client(server_params)
            self._read, self._write = await self._stdio_ctx.__aenter__()
            self._session_ctx = ClientSession(self._read, self._write)
            self.session = await self._session_ctx.__aenter__()
            await self.session.initialize()
            return self.session

        return self._run_async(_connect())

    def list_tools(self):
        """列出所有可用工具（同步）"""
        async def _list():
            result = await self.session.list_tools()
            return result.tools
        return self._run_async(_list())

    def call_tool(self, tool_name: str, arguments: dict):
        """调用工具（同步）"""
        async def _call():
            result = await self.session.call_tool(tool_name, arguments=arguments)
            return result.content[0].text if result.content else ""
        return self._run_async(_call())

    def close(self):
        async def _close():
            if self.session and self._session_ctx:
                await self._session_ctx.__aexit__(None, None, None)
            if self._stdio_ctx:
                await self._stdio_ctx.__aexit__(None, None, None)
            if self._loop and not self._loop.is_closed():
                self._loop.close()
        self._run_async(_close())

# ---------- 配置 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.join(BASE_DIR, "Workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

# 全局单例 MCP 客户端
mcp_client = MCPClient(
    command="python",
    args=["mcp_server.py"]
)