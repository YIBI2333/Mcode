"""MCP 客户端 — 独立线程事件循环"""
import asyncio
import threading
import sys
import logging
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ..paths import PROJECT_ROOT, MCP_SERVER_PATH

_log = logging.getLogger(__name__)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class MCPClient:
    def __init__(self, command: str, args: list = None):
        self.command = command
        self.args = args or []
        self.session = None
        self._stdio_ctx = None
        self._session_ctx = None
        self._read = None
        self._write = None
        self._connected = False
        self._loop = None
        self._thread = None

    def connect(self, timeout: int = 60):
        ready = threading.Event()
        _connect_task = None

        async def _connect():
            try:
                server_params = StdioServerParameters(
                    command=self.command, args=self.args, cwd=PROJECT_ROOT
                )
                # 用 async with 确保 stdio_client 在同一个 task 中正确清理
                async with stdio_client(server_params) as (read, write):
                    self._read, self._write = read, write
                    async with ClientSession(read, write) as session:
                        self.session = session
                        await self.session.initialize()
                        self._connected = True
                        ready.set()
                        # 保持连接，等待取消信号
                        try:
                            while True:
                                await asyncio.sleep(3600)
                        except asyncio.CancelledError:
                            pass
            except asyncio.CancelledError:
                pass
            except Exception:
                _log.exception("MCP 连接失败")
            finally:
                if not self._connected:
                    ready.set()

        async def _run():
            nonlocal _connect_task
            _connect_task = asyncio.current_task()
            await _connect()

        def _thread_main():
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_run())
            except Exception:
                _log.exception("MCP event loop 异常退出")
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    _log.debug("shutdown_asyncgens failed", exc_info=True)
                loop.close()

        self._thread = threading.Thread(target=_thread_main, daemon=True)
        self._thread.start()

        if not ready.wait(timeout=timeout):
            # 超时 → 取消连接任务，触发同一 task 内的 async with 清理
            if _connect_task is not None and self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(_connect_task.cancel)
                self._thread.join(timeout=5)
            print("[MCP] 客户端连接超时（60s），MCP 工具不可用。", file=sys.stderr, flush=True)

    def list_tools(self):
        if not self._connected:
            return []
        async def _list():
            r = await self.session.list_tools()
            return r.tools
        try:
            return asyncio.run_coroutine_threadsafe(_list(), self._loop).result(timeout=10)
        except Exception:
            _log.warning("list_tools failed", exc_info=True)
            return []

    def call_tool(self, tool_name: str, arguments: dict):
        if not self._connected:
            return ""
        async def _call():
            r = await self.session.call_tool(tool_name, arguments=arguments)
            return r.content[0].text if r.content else ""
        try:
            return asyncio.run_coroutine_threadsafe(_call(), self._loop).result(timeout=60)
        except Exception:
            _log.warning("call_tool failed", exc_info=True)
            return ""


mcp_client = MCPClient(command=sys.executable, args=[MCP_SERVER_PATH])
