import os
import asyncio
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types
import subprocess

from config import MCP_WORKSPACE_DIR

# ---------- 工作目录配置 ----------
WORKSPACE_DIR = MCP_WORKSPACE_DIR
if WORKSPACE_DIR:
    os.makedirs(WORKSPACE_DIR, exist_ok=True)

def safe_path(relative_path: str) -> str:
    """将路径转换为绝对路径，并根据 WORKSPACE_DIR 进行安全限制"""
    if WORKSPACE_DIR is None:
        return os.path.abspath(relative_path)
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, relative_path))
    if not full_path.startswith(os.path.abspath(WORKSPACE_DIR)):
        raise ValueError(f"路径 {relative_path} 超出工作目录范围")
    return full_path

server = Server("local-file-server")
@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_directory",
            description="列出目录内容",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要列出的路径，默认为工作区根目录"}
                }
            }
        ),
        types.Tool(
            name="read_text_file",
            description="读取文本文件",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        ),
        types.Tool(
            name="write_file",
            description="创建新文件或覆盖已有文件",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件相对路径"},
                    "content": {"type": "string", "description": "要写入的内容"}
                },
                "required": ["path", "content"]
            }
        ),
        types.Tool(
            name="delete_file",
            description="删除指定文件（不可恢复）",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件相对路径"}
                },
                "required": ["path"]
            }
        ),
        types.Tool(
            name="move_file",
            description="移动或重命名文件",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "源路径"},
                    "destination": {"type": "string", "description": "目标路径"}
                },
                "required": ["source", "destination"]
            }
        ),
        types.Tool(
            name="execute_command",
            description="在本地 shell 中执行命令（仅允许白名单命令）",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"}
                },
                "required": ["command"]
            }
        ),
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "list_directory":
            path = arguments.get("path", ".")
            full = safe_path(path)
            if not os.path.isdir(full):
                return [types.TextContent(type="text", text=f"路径不是目录: {full}")]
            items = os.listdir(full)
            return [types.TextContent(type="text", text="\n".join(items) if items else "（空目录）")]
        elif name == "read_text_file":
            path = arguments.get("path")
            if not path:
                return [types.TextContent(type="text", text="错误: 未指定路径")]
            full = safe_path(path)
            if not os.path.isfile(full):
                return [types.TextContent(type="text", text=f"文件不存在: {full}")]
            with open(full, "r", encoding="utf-8") as f:
                return [types.TextContent(type="text", text=f.read())]
        elif name == "write_file":
            path = arguments.get("path")
            content = arguments.get("content")
            full = safe_path(path)
            try:
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
                return [types.TextContent(type="text", text=f"文件写入成功: {path}")]
            except Exception as e:
                return [types.TextContent(type="text", text=f"写入失败: {e}")]
        elif name == "delete_file":
            path = arguments.get("path")
            full = safe_path(path)
            try:
                os.remove(full)
                return [types.TextContent(type="text", text=f"文件删除成功: {path}")]
            except Exception as e:
                return [types.TextContent(type="text", text=f"删除失败: {e}")]
            
        elif name == "move_file":
            src = arguments.get("source")
            dst = arguments.get("destination")
            full_src = safe_path(src)
            full_dst = safe_path(dst)
            try:
                os.rename(full_src, full_dst)
                return [types.TextContent(type="text", text=f"移动成功: {src} → {dst}")]
            except Exception as e:
                return [types.TextContent(type="text", text=f"移动失败: {e}")]
        elif name == "execute_command":
            cmd = arguments.get("command")
            # 安全白名单（只允许特定命令）
            allowed_prefixes = ("ls", "pwd", "echo", "cat", "grep", "dir", "python")
            if not any(cmd.startswith(p) for p in allowed_prefixes):
                return [types.TextContent(type="text", text="拒绝执行：命令不在白名单内")]
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                output = result.stdout + result.stderr
                return [types.TextContent(type="text", text=output or "（无输出）")]
            except Exception as e:
                return [types.TextContent(type="text", text=f"执行失败: {e}")]
        else:
            return [types.TextContent(type="text", text=f"未知工具: {name}")]

    except Exception as e:
        return [types.TextContent(type="text", text=f"错误: {e}")]

async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="local-file-server",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())