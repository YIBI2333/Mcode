"""GET /api/app-config, POST /api/app-config."""
import os, json as _json, logging
from fastapi import APIRouter, Request

_log = logging.getLogger(__name__)

_APP_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "config", "app_config.json",
)

router = APIRouter()


@router.get("/api/app-config")
async def get_app_config():
    """Get current app-level config (history length, memory limit, etc.)."""
    try:
        with open(_APP_CONFIG_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {"short_term_history_length": 5, "memory_search_limit": 3,
                "max_agent_steps": 10, "tool_choice": "auto"}


@router.post("/api/app-config")
async def save_app_config(req: Request):
    """Save app-level config (hot reload)."""
    body = await req.json()
    try:
        with open(_APP_CONFIG_PATH, "w", encoding="utf-8") as f:
            _json.dump(body, f, ensure_ascii=False, indent=2)
        _reload_settings(body)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _reload_settings(cfg):
    """Hot-reload config.settings module variables."""
    try:
        import config.settings as s
        s.reload_from_dict(cfg)
    except Exception:
        _log.warning("_reload_settings failed", exc_info=True)
