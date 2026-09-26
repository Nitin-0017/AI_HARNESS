"""Composition root for Phase 1. Deliberately imports no model or tool executor."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TextIO

from .config import load_config
from .environment import load_environment
from .errors import InputError
from .inputs import load_task, validate_task
from .state import RunState
from .telemetry import EventLog, Redactor, create_run_dir, write_state
from .workspace import Workspace, validate_output_dir


@dataclass(frozen=True)
class StartupOptions:
    config_path: Path | None = None
    overrides: Mapping[str, Any] | None = None
    task: str | None = None
    task_file: Path | None = None
    task_stdin: bool = False
    non_interactive: bool = False
    require_input: bool = False


@dataclass(frozen=True)
class StartupResult:
    state: RunState
    snapshot: dict[str, Any]
    run_dir: Path
    state_file: Path


def _prompt(stream: TextIO, output: TextIO, label: str, limit: int) -> str | None:
    print(label, file=output, end=" ", flush=True)
    value = stream.readline(limit + 1)
    if not value or not value.strip():
        return None
    if len(value) > limit:
        raise InputError("Interactive input exceeds the permitted length")
    return value.strip()


def initialize(
    options: StartupOptions, *, env: Mapping[str, str], stdin: TextIO,
    prompt_output: TextIO, project_root: Path, cwd: Path,
) -> StartupResult:
    environment = load_environment(env)
    redactor = Redactor(environment.credential.reveal())
    config = load_config(project_root, config_path=options.config_path, env=env,
                         overrides=options.overrides, cwd=cwd)
    task = load_task(text=options.task, file=options.task_file, from_stdin=options.task_stdin,
                     env=env, stdin=stdin, cwd=cwd, max_bytes=config.budgets.max_task_bytes)
    interactive = not options.non_interactive and not options.task_stdin and stdin.isatty()
    if interactive:
        if config.workspace is None:
            entered = _prompt(stdin, prompt_output, "Target workspace path (blank leaves it unset):", 4096)
            if entered:
                candidate = Path(entered).expanduser()
                config = replace(config, workspace=candidate if candidate.is_absolute() else cwd / candidate)
        if task is None:
            entered = _prompt(stdin, prompt_output, "Task text (blank leaves it unset; use --task-file for multiline text):",
                              config.budgets.max_task_bytes)
            if entered:
                task = validate_task(entered, "interactive", config.budgets.max_task_bytes)
    # Operator input time is not model/agent runtime; start the clock now.
    state = RunState(config.budgets, task=task, model=config.public_dict()["model"])
    workspace = Workspace.open(config.workspace, project_root) if config.workspace is not None else None
    if workspace:
        state.workspace = str(workspace.root)
    if task:
        if len(task.text) > config.budgets.max_context_chars:
            raise InputError("Task exceeds the configured max_context_chars limit")
        state.usage.context_chars = len(task.text)
    state.finish_startup()
    if options.require_input and state.missing_inputs:
        raise InputError("A task and separate workspace are required with --require-input")
    output_dir = validate_output_dir(config.output_dir, workspace=workspace, harness_root=project_root)
    run_dir = create_run_dir(output_dir, state.run_id)
    with EventLog(run_dir, state.run_id, redactor, config.log_level) as events:
        events.emit("startup.begin", phase="PROJECT_FOUNDATION")
        events.emit("environment.validated", credential_present=environment.credential_present, credential_source="environment")
        events.emit("configuration.loaded", configuration=config.public_dict())
        events.emit("input.loaded", workspace=state.workspace, task_present=task is not None,
                    task_bytes=task.size_bytes if task else 0, missing_inputs=state.missing_inputs)
        try:
            state.check_time_budget()
            snapshot = redactor.clean(state.to_dict())
            snapshot["artifacts"] = {"run_dir": redactor.text(str(run_dir)),
                                     "state_file": redactor.text(str(run_dir / "run_state.json")),
                                     "events_file": redactor.text(str(run_dir / "events.jsonl"))}
            state_file = write_state(run_dir, snapshot, redactor)
            events.emit("startup.complete", status=state.status.value,
                        verification_status="NOT_RUN", model_calls=state.usage.model_calls)
        except Exception:
            events.emit("startup.failed", level="ERROR", error="Failed to finalize foundation state")
            raise
    return StartupResult(state, snapshot, run_dir, state_file)
