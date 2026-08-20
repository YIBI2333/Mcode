"""Web server — FastAPI app + static files + route registration."""
import sys, os, logging, threading, time, subprocess, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

_log = logging.getLogger(__name__)

app = FastAPI()

# Electron 退出时通过 /api/shutdown 通知后端，主线程据此退出
_shutdown_event = threading.Event()


def shutdown_event():
    return _shutdown_event


def request_shutdown():
    _shutdown_event.set()


@app.post("/api/shutdown")
async def api_shutdown():
    request_shutdown()
    return {"ok": True}

# ---- Static file mounts ----
_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live2d", "web")
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
_tts_dir   = os.path.join(_DATA_DIR, "tts_cache")
_upload_dir = os.path.join(_DATA_DIR, "uploads")
_screenshot_dir = os.path.join(_DATA_DIR, "screenshots")

os.makedirs(_tts_dir, exist_ok=True)
os.makedirs(_upload_dir, exist_ok=True)
os.makedirs(_screenshot_dir, exist_ok=True)

app.mount("/model", StaticFiles(directory=_WEB_DIR), name="model")
app.mount("/live2d-resources", StaticFiles(directory=os.path.join(_WEB_DIR, "resources")), name="live2d-resources")
app.mount("/uploads", StaticFiles(directory=_upload_dir), name="uploads")
app.mount("/tts-audio", StaticFiles(directory=_tts_dir), name="tts-audio")
app.mount("/screenshots", StaticFiles(directory=_screenshot_dir), name="screenshots")

# 工作区静态挂载（供图片缩略图/文件预览访问），仅当配置了工作区目录
try:
    from config.settings import MCP_WORKSPACE_DIR as _WS_DIR
except Exception:
    _WS_DIR = None
if _WS_DIR:
    os.makedirs(_WS_DIR, exist_ok=True)
    app.mount("/workspace", StaticFiles(directory=_WS_DIR), name="workspace")

# ---- Route registration ----
from src.server.routes.chat import router as chat_router
from src.server.routes.pet import router as pet_router
from src.server.routes.config import router as config_router
from src.server.routes.tts import router as tts_router
from src.server.routes.history import router as history_router
from src.server.routes.tasks import router as tasks_router

app.include_router(chat_router)
app.include_router(pet_router)
app.include_router(config_router)
app.include_router(tts_router)
app.include_router(history_router)
app.include_router(tasks_router)


# ---- File upload ----
@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "file.bin")[1].lower()
    safe_name = f"upload_{int(time.time() * 1000)}{ext}"
    save_path = os.path.join(_upload_dir, safe_name)
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        return JSONResponse({"error": "文件超过 50MB 限制"}, status_code=400)
    with open(save_path, "wb") as f:
        f.write(content)
    url = f"/uploads/{safe_name}"
    is_image = ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    return {"ok": True, "path": save_path, "url": url, "name": safe_name, "is_image": is_image}


# ---- Static HTML pages ----
_html = lambda name: FileResponse(os.path.join(_WEB_DIR, name), media_type="text/html",
                                   headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})

@app.get("/actions", response_class=FileResponse)
async def action_bar():           return _html("action_bar.html")

@app.get("/chat-panel", response_class=FileResponse)
async def chat_panel():           return _html("chat_panel.html")

@app.get("/settings", response_class=FileResponse)
async def settings_page():        return _html("settings.html")

@app.get("/expression-panel", response_class=FileResponse)
async def expression_panel():     return _html("expression_panel.html")

@app.get("/test-panel", response_class=FileResponse)
async def test_panel():           return _html("test_panel.html")

@app.get("/tasks-panel", response_class=FileResponse)
async def tasks_panel():          return _html("task_panel.html")

@app.get("/agent", response_class=FileResponse)
async def agent_window():         return _html("index.html")


# ---- Server runner ----
def start_web(port: int = 8866):
    """启动 web 服务（后台线程）+ 初始化（压缩/心跳），确认就绪后返回 uvicorn.Server。

    供 main() 在启动 Electron 之前调用，确保 Electron 加载页面时 web 已就绪。
    """
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def _silence_conn_reset(loop, context):
        """静默客户端断开连接（Electron 退出）时的 ConnectionResetError 噪音日志"""
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return
        loop.default_exception_handler(context)

    def _serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(_silence_conn_reset)
        try:
            loop.run_until_complete(server.serve())
        except Exception:
            _log.exception("uvicorn server.run() exited with error")
        finally:
            loop.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.1)
    print(f"[Web] server started (port {port})")

    from src.memory.compress import startup_init
    startup_init()

    from src.server.routes.shared import start_heartbeat
    start_heartbeat()

    return server


def run_web(port: int = 8866, electron_proc=None):
    """等待 Electron 退出，然后关闭 web 服务。"""
    server = start_web(port)

    if electron_proc:
        # 等 Electron 退出，但加超时：electron_proc 是 npx 启动的中间进程，
        # Electron 退出后 npx 可能残留，导致 wait() 永久阻塞（Python 卡死、定时任务还在跑）
        try:
            electron_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _log.warning("等待 Electron 退出超时（npx 可能残留），继续关闭")
        except KeyboardInterrupt:
            raise
        except Exception:
            _log.exception("error waiting for Electron process")
        print("[Web] Electron exited, shutting down...")
    server.should_exit = True
    # uvicorn 运行在 daemon 线程，主线程退出后自动结束，无需精确 join
    print("[Web] shut down")
