"""Live2D 智能体 — Electron 壳启动器
   
启动方式: python main.py
（需要先安装 Electron: npm install）
"""
import subprocess
import os
import shutil
import sys


def _find_cmd(name: str):
    """Windows 上优先找 .cmd，其他平台直接用 name"""
    if sys.platform == "win32":
        # shutil.which 在 Windows 上支持 PATHEXT，会自动找 .cmd/.exe
        path = shutil.which(f"{name}.cmd") or shutil.which(name)
        return path
    else:
        return shutil.which(name)


def launch_live2d_mcp():
    """启动 Live2D MCP Server（渲染器 + MCP 工具），返回 subprocess"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    live2d_mcp_dir = os.path.join(project_root, "src", "live2d", "web")

    if not os.path.isdir(os.path.join(live2d_mcp_dir, "node_modules")):
        print("[Live2D MCP] 依赖未安装，请在 src/live2d/web/ 下运行 npm install")
        return None

    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        print("[Live2D MCP] 未找到 Node.js")
        return None

    try:
        proc = subprocess.Popen(
            [node, "server.js"],
            cwd=live2d_mcp_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[Live2D MCP] Server 已启动 (PID: {proc.pid}) → http://localhost:8850")
        return proc
    except Exception as e:
        print(f"[Live2D MCP] 启动失败: {e}")
        return None


def launch_vite():
    """启动 Vite dev server（Prometheus Avatar 渲染器用），返回 subprocess"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    live2d_mcp_dir = os.path.join(project_root, "src", "live2d", "web")
    npx = _find_cmd("npx")
    if not npx: return None
    try:
        proc = subprocess.Popen([npx, "vite", "--port", "5188", "--force"], cwd=live2d_mcp_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[Vite] Prometheus Avatar 渲染器已启动 → http://localhost:5188")
        return proc
    except Exception as e:
        print(f"[Vite] 启动失败: {e}")
        return None


def launch_electron(server_url: str = "http://localhost:8866", mcp_mode: bool = True, mcp_renderer_url: str = "http://localhost:5188"):
    """启动 Electron 桌面窗口（非阻塞）

    mcp_mode=True: Live2D 用 Prometheus Avatar 渲染器
    mcp_renderer_url: MCP 渲染器地址（默认 localhost:5188）
    """
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main_js = os.path.join(project_root, "src", "electron", "main.js")

    # 检查 node_modules 是否已安装
    node_modules = os.path.join(project_root, "node_modules", "electron")
    if not os.path.isdir(node_modules):
        print("[Desktop Pet] 未检测到 node_modules，正在安装 Electron（约 100MB，仅需一次）...")
        npm = _find_cmd("npm")
        if not npm:
            print("[Desktop Pet] 未找到 npm，请先安装 Node.js: https://nodejs.org")
            return None
        try:
            subprocess.run(
                [npm, "install"],
                cwd=project_root,
                check=True,
            )
            print("[Desktop Pet] Electron 安装完成")
        except subprocess.CalledProcessError as e:
            print(f"[Desktop Pet] npm install 失败，请手动运行: npm install")
            return None

    # 直接用 Electron 可执行文件（避免 npx 中间进程）
    # 关键：用 npx 时，npx 作为父进程在应用运行期间一直存活，electron_proc.wait() 永不返回，
    # 导致主进程卡死或误判超时自关。直接用 electron.exe，wait() 才能准确反映 Electron 退出。
    electron_exe = os.path.join(project_root, "node_modules", "electron", "dist", "electron.exe")
    if not os.path.exists(electron_exe):
        electron_exe = os.path.join(project_root, "node_modules", "electron", "dist", "electron")
    if not os.path.exists(electron_exe):
        print("[Desktop Pet] 未找到 Electron 可执行文件，请确认 npm install 完成")
        return None

    # 启动 Electron（后台进程）
    try:
        env = os.environ.copy()
        env["PET_SERVER_URL"] = server_url
        if mcp_mode:
            env["LIVE2D_MCP"] = "true"
            env["MCP_RENDERER_URL"] = mcp_renderer_url

        proc = subprocess.Popen(
            [electron_exe, main_js],
            cwd=project_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[Desktop Pet] Electron 桌面窗口已启动 (PID: {proc.pid})")
        return proc
    except Exception as e:
        print(f"[Desktop Pet] 启动失败: {e}")
        return None
