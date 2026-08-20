"""Mcoderun"""

import sys
import os

# ═══ 必须在所有项目导入之前设置（mem0 初始化 PostHog 需要） ═══
os.environ.setdefault("POSTHOG_DISABLED", "1")
os.environ.setdefault("POSTHOG_HOST", "http://127.0.0.1:1")
os.environ.setdefault("POSTHOG_API_KEY", "")
os.environ.setdefault("MEM0_TELEMETRY", "false")
os.environ.setdefault("MEM0_CLIENT_TELEMETRY", "false")

import signal
import atexit
import subprocess
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.memory.engine import retrieve_context
from src.tools.adapter import init_mcp
from src.server.launcher import launch_electron, launch_live2d_mcp
from src.server.win_job import init_job, add_to_job, job_ok

SERVER_URL = "http://localhost:8866"
MCP_RENDERER_URL = "http://localhost:8850"

_subprocesses: list = []


def _kill_procs():
    for p in _subprocesses:
        if p is None or p.poll() is not None:
            continue
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(0.5)
    for p in _subprocesses:
        if p is None or p.poll() is not None:
            continue
        try:
            # Windows：强制杀整个进程树（含子进程，如 npx → electron）
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True, timeout=5)
            else:
                p.kill()
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    # 兜底：Electron 渲染/GPU 子进程可能残留为孤儿进程（poll 已非 None，上面的循环会跳过）。
    # Job Object 生效时由 OS 自动清理整棵进程树（且避免误杀用户同时运行的其他 Electron 应用）；
    # Job 不可用（创建失败等）时，退回按映像名杀光所有 electron 进程。
    if sys.platform == "win32" and not job_ok():
        for img in ("electron.exe",):
            try:
                subprocess.run(["taskkill", "/F", "/IM", img, "/T"],
                               capture_output=True, timeout=5)
            except Exception:
                pass


def _on_exit():
    _kill_procs()


atexit.register(_on_exit)


def _signal_handler(signum, _frame):
    name = signal.Signals(signum).name
    print(f"\n[{name}] shutting down...", flush=True)
    _kill_procs()
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def main():
    print("Mcode | starting...\n")

    # Windows Job Object：本进程无论以何种方式退出（正常/崩溃/被强杀/终端被关），
    # OS 都会自动杀死 Job 内所有子进程，防止 Electron 窗口等残留
    init_job()

    print("[mem0] loading memory engine...")
    retrieve_context("system", "preload", limit=1)
    print("[mem0] ready\n")

    from src.scheduler.engine import start_scheduler
    start_scheduler()

    print("[Live2D] starting MCP render server...")
    mcp_proc = launch_live2d_mcp()
    if mcp_proc:
        _subprocesses.append(mcp_proc)
        add_to_job(mcp_proc)
        print("[Live2D] MCP render server ready\n")
    else:
        print("[Live2D] WARNING: MCP render server failed to start\n")

    print("[MCP] connecting tool server...")
    init_mcp()
    print("[MCP] ready\n")

    # 先启动 web 服务并确认就绪（Electron 加载页面依赖 web，必须先起来）
    print("[Web] starting web server...")
    from src.server.app import start_web
    server = start_web(port=8866)

    print("[Electron] starting Electron...")
    electron_proc = launch_electron(
        server_url=SERVER_URL,
        mcp_mode=True,
        mcp_renderer_url=MCP_RENDERER_URL,
    )
    if not electron_proc:
        print("Electron failed to start.")
        _kill_procs()
        os._exit(1)
    _subprocesses.append(electron_proc)
    add_to_job(electron_proc)

    try:
        # 主循环：等待退出信号（Electron 调 /api/shutdown）或 Electron 进程退出
        # 用非阻塞 poll 轮询，避免 wait() 卡死；正常运行时不退出（无超时）
        from src.server.app import shutdown_event
        while True:
            if shutdown_event().is_set():
                break
            if electron_proc and electron_proc.poll() is not None:
                break
            time.sleep(0.3)
        print("[Web] Electron exited, shutting down...")
        server.should_exit = True
        print("[Web] shut down")
    except KeyboardInterrupt:
        pass
    finally:
        _kill_procs()
        # 强制退出 Python：确保定时任务/心跳等后台线程彻底停止，
        # 避免 Electron 关闭后进程残留
        os._exit(0)


if __name__ == "__main__":
    main()
