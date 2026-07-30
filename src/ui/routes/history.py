"""GET /api/chat/history, DELETE /api/chat/history."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/chat/history")
async def get_chat_history(limit: int = 10, skip: int = 0):
    """Get chat history (paginated).

    skip=0, limit=10  -> return last 10 entries
    skip=10, limit=10 -> return entries 20~11 from end
    """
    from src.infra.messages import get_history
    history = get_history()
    total = len(history)
    end = total - skip
    start = max(0, end - limit)
    return {"history": history[start:end], "total": total}


@router.delete("/api/chat/history")
async def clear_chat_history():
    """Clear chat display history (不影响短期记忆窗口)."""
    from src.infra.messages import clear_history
    clear_history()
    return {"ok": True}
