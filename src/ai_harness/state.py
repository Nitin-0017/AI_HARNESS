"""Structured foundation state. READY means initialized, never task-complete."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Any
from uuid import uuid4

from .config import BudgetConfig
from .errors import BudgetExceeded
from .inputs import TaskInput


class StartupStatus(str, Enum):
    STARTING = "STARTING"
    AWAITING_INPUT = "AWAITING_INPUT"
    READY = "READY"


class AgentStatus(str, Enum):
    START = "START"
    INSPECTING = "INSPECTING"
    PLANNING = "PLANNING"
    ACTING = "ACTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INCOMPLETE = "INCOMPLETE"


@dataclass
class ResourceUsage:
    inspection_reads: int = 0
    inspection_bytes: int = 0
    search_calls: int = 0
    read_calls: int = 0
    edit_calls: int = 0
    cache_hits: int = 0
    avoided_test_runs: int = 0
    model_seconds: float = 0.0
    tool_seconds: float = 0.0
    test_seconds: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    test_executions: int = 0
    iterations: int = 0
    failures: int = 0
    recovery_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    context_chars: int = 0
    context_bytes: int = 0
    peak_context_chars: int = 0
    peak_context_bytes: int = 0
    context_tokens: int | None = None
    context_token_source: str = "unavailable"
    context_estimated_tokens: int = 0
    context_estimation_method: str = "not_measured"
    context_memory_bytes: int = 0
    context_items: int = 0
    context_selections: int = 0
    estimated_tokens: int = 0
    unknown_model_usage_calls: int = 0
    unknown_input_usage_calls: int = 0
    unknown_output_usage_calls: int = 0


@dataclass
class RunState:
    budgets: BudgetConfig
    run_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: StartupStatus = StartupStatus.STARTING
    task: TaskInput | None = field(default=None, repr=False)
    workspace: str | None = None
    model: dict[str, Any] = field(default_factory=dict)
    usage: ResourceUsage = field(default_factory=ResourceUsage)
    missing_inputs: list[str] = field(default_factory=list)
    credential_present: bool = True
    current_action: str | None = None
    verification_status: str = "NOT_RUN"
    model_execution: str = "NOT_RUN"
    agent_status: AgentStatus | None = None
    task_result: str | None = None
    modified_files: list[str] = field(default_factory=list)
    recovery: dict[str, Any] = field(default_factory=dict, repr=False)
    failure_fingerprints: dict[str, int] = field(default_factory=dict)
    repository_intelligence: dict[str, Any] = field(default_factory=dict, repr=False)
    verification: dict[str, Any] = field(default_factory=dict, repr=False)
    check_history: list[dict[str, Any]] = field(default_factory=list, repr=False)
    check_snapshot: str | None = None
    context: dict[str, Any] = field(default_factory=dict, repr=False)
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _started_clock: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._started_clock = self._clock()

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started_clock)

    def check_time_budget(self) -> None:
        if self.elapsed_seconds >= self.budgets.max_seconds:
            raise BudgetExceeded("Run reached the configured max_seconds limit")

    def finish_startup(self) -> None:
        self.check_time_budget()
        self.missing_inputs = []
        if self.workspace is None:
            self.missing_inputs.append("workspace")
        if self.task is None:
            self.missing_inputs.append("task")
        self.status = StartupStatus.AWAITING_INPUT if self.missing_inputs else StartupStatus.READY

    def to_dict(self) -> dict[str, Any]:
        """Call the redactor before writing/displaying this snapshot."""
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "phase": "AGENT_CONTROLLER" if self.agent_status else "MODEL_ADAPTER",
            "agent_status": self.agent_status.value if self.agent_status else None,
            "status": self.status.value,
            "credential_present": self.credential_present,
            "workspace": self.workspace,
            "task": ({"text": self.task.text, "source": self.task.source,
                      "size_bytes": self.task.size_bytes} if self.task else None),
            "context": self.context,
            "repository_intelligence": self.repository_intelligence,
            "recovery": self.recovery,
            "modified_files": self.modified_files,
            "failure_fingerprints": self.failure_fingerprints,
            "verification": self.verification,
            "check_history": self.check_history,
            "model": self.model,
            "model_execution": self.model_execution,
            "current_action": self.current_action,
            "verification_status": self.verification_status,
            "task_result": self.task_result,
            "missing_inputs": list(self.missing_inputs),
            "usage": {**asdict(self.usage), "elapsed_seconds": round(self.elapsed_seconds, 6)},
            "budgets": asdict(self.budgets),
            "note": (("Agent result is based on actual configured checks, not model claims; verification is limited to the recorded workspace snapshot." if self.agent_status else "Explicit tools/model steps are available. No autonomous task-verification verdict is implemented.")
                     if self.usage.tool_calls or self.usage.model_calls else
                     "Startup initialized inputs only. No model calls, target edits, or target checks were performed."),
        }
