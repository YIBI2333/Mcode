"""Agent 工作状态 — 追踪多步骤任务的执行进度与情绪"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AgentPhase(str, Enum):
    """Agent 工作流阶段"""
    IDLE = "idle"         # 待命中
    PLAN = "plan"         # 分析任务、制定计划
    EXECUTE = "execute"   # 逐步执行
    REFLECT = "reflect"   # 反思总结
    DONE = "done"         # 完成


@dataclass
class StepRecord:
    """单步操作记录"""
    step_num: int
    action: str          # 如 "list_directory(Workspace)"
    result: str           # 工具返回结果（截断）
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


@dataclass
class TaskContext:
    """一次复杂任务的完整上下文

    追踪：
      - 用户原始请求
      - 执行计划步骤
      - 已完成步骤
      - 当前工作流阶段
      - 总步数
    """
    original_request: str = ""
    steps: list[StepRecord] = field(default_factory=list)
    plan_steps: list[str] = field(default_factory=list)   # 规划步骤描述
    phase: AgentPhase = AgentPhase.IDLE
    total_steps: int = 0
    has_spoken: bool = False  # execute 阶段是否产生过中间回复

    # ── ReAct 控制字段 ──
    accumulated_reply: str = ""          # 任务期间所有 speak/finish 输出
    has_executed_real_tool: bool = False  # 是否执行过非控制工具（plan/speak 不算）
    finished: bool = False                # 收到 finish 工具时置位
    final_summary: str = ""               # finish/reflect(action=finish) 的摘要
    finish_requested: bool = False        # 已注入"请调 finish"引导（两段式收尾用）
    # 文件变更跟踪: {path: {"op": "edit|create|delete|move", "added": n, "deleted": n}}
    changed_files: dict = field(default_factory=dict)

    def record_file_change(self, op: str, path: str, added: int, deleted: int) -> None:
        path = (path or "").strip() or "(unknown)"
        if path in self.changed_files:
            prev = self.changed_files[path]
            self.changed_files[path] = {
                "op": op,
                "added": prev["added"] + added,
                "deleted": prev["deleted"] + deleted,
            }
        else:
            self.changed_files[path] = {"op": op, "added": added, "deleted": deleted}

    def files_summary(self) -> dict:
        """任务结束时汇总文件变更，供 task-end meta / 前端展示"""
        total_add = sum(v["added"] for v in self.changed_files.values())
        total_del = sum(v["deleted"] for v in self.changed_files.values())
        return {
            "files_total": len(self.changed_files),
            "added": total_add,
            "deleted": total_del,
            "changed_files": dict(self.changed_files),
        }

    def set_phase(self, phase: AgentPhase) -> None:
        self.phase = phase

    def set_phase_plan(self) -> None:
        self.phase = AgentPhase.PLAN

    def set_phase_execute(self) -> None:
        self.phase = AgentPhase.EXECUTE

    def set_phase_done(self) -> None:
        self.phase = AgentPhase.DONE

    def mark_real_tool_executed(self) -> None:
        self.has_executed_real_tool = True

    def add_reply(self, text: str) -> None:
        if text:
            self.accumulated_reply += text

    def add_step(self, action: str, result: str) -> None:
        self.steps.append(StepRecord(
            step_num=len(self.steps) + 1,
            action=action,
            result=result[:200],  # 截断，避免消息过长
        ))

    def progress_summary(self) -> str:
        """生成行动摘要，注入到后续 LLM 消息中"""
        if not self.steps:
            return ""
        lines = ["[已完成的操作]"]
        for s in self.steps:
            lines.append(f"  步骤{s.step_num}: {s.action} → {s.result[:80]}")
        return "\n".join(lines)

    def is_empty(self) -> bool:
        return len(self.steps) == 0

    def progress_ratio(self) -> float:
        """返回进度比例 0~1"""
        if not self.plan_steps:
            return 0.0
        return min(1.0, len(self.steps) / len(self.plan_steps))
