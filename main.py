"""Mcoderun"""

import sys
import os
import signal
import atexit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.memory import retrieve_context
from src.tools.mcp_tools import init_mcp
from src.ui.web import run_web
from src.ui.agent_launcher import launch_electron, launch_live2d_mcp

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
    for p in _subprocesses:
        if p is None or p.poll() is not None:
            continue
        try:
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
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

    print("[mem0] loading memory engine...")
    retrieve_context("system", "preload", limit=1)
    print("[mem0] ready\n")

    print("[Live2D] starting MCP render server...")
    mcp_proc = launch_live2d_mcp()
    if mcp_proc:
        _subprocesses.append(mcp_proc)
        print("[Live2D] MCP render server ready\n")
    else:
        print("[Live2D] WARNING: MCP render server failed to start\n")

    print("[MCP] connecting tool server...")
    init_mcp()
    print("[MCP] ready\n")

    print("[Electron] starting Electron...")
    electron_proc = launch_electron(
        server_url=SERVER_URL,
        mcp_mode=True,
        mcp_renderer_url=MCP_RENDERER_URL,
    )
    if not electron_proc:
        print("Electron failed to start.")
        _kill_procs()
        return
    _subprocesses.append(electron_proc)

    try:
        run_web(port=8866, electron_proc=electron_proc)
    except KeyboardInterrupt:
        pass
    finally:
        _kill_procs()


if __name__ == "__main__":
    main()
