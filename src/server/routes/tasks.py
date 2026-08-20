"""定时任务管理 API + 页面"""

import json as _json
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from src.scheduler.engine import list_tasks, add_task, delete_task, update_task

router = APIRouter()


@router.get("/tasks-panel")
async def tasks_panel():
    """任务管理页面"""
    import os as _os
    _dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    path = _os.path.join(_dir, "src", "live2d", "web", "task_panel.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/api/tasks")
async def api_list_tasks():
    return JSONResponse(list_tasks())


@router.post("/api/tasks")
async def api_add_task(req: Request):
    body = await req.json()
    name = body.get("name", "").strip()
    schedule = body.get("schedule", {})
    prompt = body.get("prompt", "").strip()
    if not name or not prompt:
        return JSONResponse({"error": "name 和 prompt 不能为空"}, status_code=400)
    task = add_task(name, schedule, prompt)
    return JSONResponse(task)


@router.put("/api/tasks/{task_id}")
async def api_update_task(task_id: str, req: Request):
    """更新任务（toggle enabled 或修改字段）"""
    body = await req.json()
    task = update_task(task_id, body)
    if task is None:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return JSONResponse(task)


@router.delete("/api/tasks/{task_id}")
async def api_delete_task(task_id: str):
    ok = delete_task(task_id)
    if ok:
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "任务不存在"}, status_code=404)
