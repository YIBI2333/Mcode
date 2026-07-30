"""Desktop API server — provides pages/API for Electron shell."""
import sys, os, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

_log = logging.getLogger(__name__)

# ---- App factory ----
app = FastAPI()

# ---- Static file mounts ----
app.mount("/model", StaticFiles(directory="live2d/model"), name="model")
app.mount("/live2d-resources", StaticFiles(directory="live2d/model/resources"), name="live2d-resources")

# ---- Uploads dir ----
_upload_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "uploads",
)
os.makedirs(_upload_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_upload_dir), name="uploads")

# ---- TTS audio mount (dir managed by routes/shared.py) ----
_tts_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "tts_cache",
)
os.makedirs(_tts_dir, exist_ok=True)
app.mount("/tts-audio", StaticFiles(directory=_tts_dir), name="tts-audio")

# ---- Register route modules ----
from src.ui.routes.chat import router as chat_router
from src.ui.routes.pet import router as pet_router
from src.ui.routes.config_routes import router as config_router
from src.ui.routes.tts import router as tts_router
from src.ui.routes.emotion import router as emotion_router
from src.ui.routes.history import router as history_router
from src.ui.routes.app_config_routes import router as app_config_router

app.include_router(chat_router)
app.include_router(pet_router)
app.include_router(config_router)
app.include_router(tts_router)
app.include_router(emotion_router)
app.include_router(history_router)
app.include_router(app_config_router)


# ---- Deprecated route ----
@app.get("/agent")
async def agent_window():
    """Migrated to live2d_mcp/ (Prometheus Avatar MCP renderer)."""
    return {
        "deprecated": True,
        "message": "Live2D rendering migrated to live2d_mcp/ MCP mode",
        "mcp_dev_url": "http://localhost:5188",
        "mcp_prod_url": "http://localhost:8850",
        "setup": "cd live2d_mcp && npm run dev",
    }


# ---- File upload ----
@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    """上传文件（图片或其他），返回可访问的路径"""
    import time as _time
    ext = os.path.splitext(file.filename or "file.bin")[1].lower()
    ts = int(_time.time() * 1000)
    safe_name = f"upload_{ts}{ext}"
    save_path = os.path.join(_upload_dir, safe_name)
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50MB
        return JSONResponse({"error": "文件超过 50MB 限制"}, status_code=400)
    with open(save_path, "wb") as f:
        f.write(content)
    url = f"/uploads/{safe_name}"
    is_image = ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    return {"ok": True, "path": save_path, "url": url, "name": safe_name, "is_image": is_image}


# ---- Static HTML pages ----
@app.get("/actions", response_class=FileResponse)
async def action_bar():
    return FileResponse(
        "live2d/model/action_bar.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/chat-panel", response_class=FileResponse)
async def chat_panel():
    return FileResponse(
        "live2d/model/chat_panel.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/settings", response_class=FileResponse)
async def settings_page():
    return FileResponse(
        "live2d/model/settings.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/expression-panel", response_class=FileResponse)
async def expression_panel_page():
    return FileResponse(
        "live2d/model/expression_panel.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/test-panel", response_class=FileResponse)
async def test_panel_page():
    return FileResponse(
        "live2d/model/test_panel.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# ---- Server runner ----
def run_web(port: int = 8866, electron_proc=None):
    """Start web server (for Electron desktop pages/API).

    When electron_proc is given, monitors the process and shuts down
    the server when it exits.
    """
    import threading
    import time

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def _serve():
        try:
            server.run()
        except Exception:
            _log.exception("uvicorn server.run() exited with error")

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    while not server.started:
        time.sleep(0.1)

    print(f"[Web] server started (port {port})")

    # 启动后初始化：压缩记忆、打招呼、心跳
    try:
        from src.infra.messages import startup_init
        startup_init()
    except Exception:
        _log.debug("startup_init failed", exc_info=True)

    try:
        from src.ui.routes.shared import start_heartbeat
        start_heartbeat()
    except Exception:
        _log.debug("start_heartbeat failed", exc_info=True)

    if electron_proc:
        try:
            electron_proc.wait()
        except KeyboardInterrupt:
            raise
        except Exception:
            _log.exception("error waiting for Electron process")
        print("[Web] Electron exited, shutting down...")

    server.should_exit = True
    try:
        thread.join(timeout=5)
    except Exception:
        _log.exception("error joining web thread")
    print("[Web] shut down")
