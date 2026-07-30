"""GET /api/model-config, POST /api/model-config."""
import os, json as _json, logging
from fastapi import APIRouter, Request

_log = logging.getLogger(__name__)

_MODEL_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "config", "model_config.json",
)

router = APIRouter()


@router.get("/api/model-config")
async def get_model_config():
    """Get current model configuration."""
    try:
        with open(_MODEL_CONFIG_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {"providers": [], "roles": {}}


@router.post("/api/model-config")
async def save_model_config(req: Request):
    """Save model configuration (hot reload)."""
    body = await req.json()
    try:
        with open(_MODEL_CONFIG_PATH, "w", encoding="utf-8") as f:
            _json.dump(body, f, ensure_ascii=False, indent=2)
        _reload_api_config(body)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _reload_api_config(cfg):
    """Hot-reload api_config module variables."""
    try:
        import config.api_config as ac
        providers = {p["id"]: p for p in cfg.get("providers", [])}
        defaults = [p for p in cfg.get("providers", []) if p.get("is_default")]

        def _resolve_first_entry(role_cfg):
            if not role_cfg:
                return None, None
            for entry in role_cfg.get("entries", []):
                if not entry.get("enabled"):
                    continue
                pids = entry.get("provider_ids") or []
                if not pids:
                    pid = entry.get("provider_id", "")
                    if pid:
                        pids = [pid]
                if not pids:
                    continue
                return pids[0], entry.get("model", "")
            return None, None

        def _apply(role_key, prefix):
            pid, model = _resolve_first_entry(cfg.get("roles", {}).get(role_key))
            if pid and model:
                p = providers.get(pid, {})
                setattr(ac, f"{prefix}_LLM_KEY", p.get("api_key", ""))
                setattr(ac, f"{prefix}_LLM_MODEL", model)
                setattr(ac, f"{prefix}_LLM_BASE_URL", p.get("base_url", ""))
                return
            if defaults:
                dp = defaults[0]
                setattr(ac, f"{prefix}_LLM_KEY", dp.get("api_key", ""))
                setattr(ac, f"{prefix}_LLM_MODEL", dp.get("default_model", "") or dp.get("name", ""))
                setattr(ac, f"{prefix}_LLM_BASE_URL", dp.get("base_url", ""))

        _apply("chat", "CHAT")
        _apply("memory", "MEMORY")
        _apply("tool", "TOOL")
        _apply("vision", "VISION")

        pid, model = _resolve_first_entry(cfg.get("roles", {}).get("memory"))
        if pid:
            p = providers.get(pid, {})
            os.environ["OPENAI_API_KEY"] = p.get("api_key", "")
            os.environ["OPENAI_BASE_URL"] = p.get("base_url", "")
        elif defaults:
            os.environ["OPENAI_API_KEY"] = defaults[0].get("api_key", "")
            os.environ["OPENAI_BASE_URL"] = defaults[0].get("base_url", "")
    except Exception:
        _log.warning("_reload_api_config failed", exc_info=True)


@router.post("/api/test-model")
async def test_model(req: Request):
    """Test model connectivity with a minimal request (1 token)."""
    body = await req.json()
    base_url = body.get("base_url", "").strip()
    api_key = body.get("api_key", "").strip()
    model = body.get("model", "").strip()
    if not base_url or not api_key or not model:
        return {"ok": False, "error": "缺少 base_url / api_key / model"}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=10)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            temperature=0,
        )
        return {"ok": True, "model": resp.model}
    except Exception as e:
        msg = str(e)[:200]
        return {"ok": False, "error": msg}
