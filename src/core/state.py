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

    def set_phase(self, phase: AgentPhase) -> None:
        self.phase = phase

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
