"""程序入口"""
from ui.cli import run_cli
from core.memory import retrieve_context
from tools.mcp_tools import init_mcp

if __name__ == "__main__":
    
    # 预热启动
    # 初始化 mem0
    print("正在加载mem0...")
    retrieve_context("system", "preload", limit=1)
    print("mem0启动完成。\n")
    
    # 初始化 MCP
    print("正在连接 MCP 服务器...")
    init_mcp()
    print("MCP 就绪\n")
    
    run_cli()