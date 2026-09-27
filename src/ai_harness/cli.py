"""A lightweight terminal entry point, not a simulated coding agent."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
from typing import Any, TextIO

from . import __version__
from .config import BudgetConfig, FLOAT_BUDGETS
from dataclasses import fields
from .errors import BudgetExceeded, ConfigurationError, HarnessError
from .startup import StartupOptions, initialize
from .telemetry import Redactor


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if message.startswith("unrecognized arguments:"):
            # Unknown arguments may themselves contain a mistakenly supplied key.
            raise ConfigurationError("Unsupported command-line option. Credentials belong only in AI_API_KEY; see --help.")
        raise ConfigurationError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(description="AI Harness Phase 2: initialize state and execute requested repository tools. No model execution.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, help="Trusted TOML file (default: harness.toml in the harness project)")
    parser.add_argument("--workspace", "--repo", dest="workspace", type=Path, help="Existing, separate target directory")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--task", help="Task text; no URL is automatically fetched")
    inputs.add_argument("--task-file", type=Path, help="UTF-8 issue/task file")
    inputs.add_argument("--task-stdin", action="store_true", help="Read bounded task text from standard input")
    parser.add_argument("--output-dir", type=Path, help="Harness-owned logs; may not overlap the target")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    parser.add_argument("--model-family", choices=("deepseek", "qwen"))
    parser.add_argument("--model-id", "--model", dest="model_id")
    parser.add_argument("--endpoint", help="Reserved for later integration; not contacted")
    parser.add_argument("--request-format")
    parser.add_argument("--response-format")
    for item in fields(BudgetConfig):
        parser.add_argument("--" + item.name.replace("_", "-"),
                            type=float if item.name in FLOAT_BUDGETS else int)
    parser.add_argument("--non-interactive", action="store_true", help="Never prompt for missing inputs")
    parser.add_argument("--require-input", action="store_true", help="Return an error when workspace/task is missing")
    parser.add_argument("--json", action="store_true", help="Print the redacted state as JSON")
    parser.add_argument("--tool", choices=("list_files", "search_code", "read_file", "apply_patch", "run_checks", "get_changes"),
                        help="Execute exactly one real tool; no autonomous model loop")
    parser.add_argument("--tool-args", default="{}", help="JSON object containing tool arguments")
    return parser


def _terminal_text(value: Any) -> str:
    """Prevent task/path text from injecting terminal control sequences."""
    text = str(value)
    return "".join(char if char.isprintable() else char.encode("unicode_escape").decode("ascii") for char in text)


def _display(snapshot: dict[str, Any], output: TextIO) -> None:
    model = snapshot["model"]
    task = snapshot["task"]
    usage = snapshot["usage"]
    preview = task["text"].replace("\n", " ")[:180] if task else "Not supplied"
    lines = [
        "AI Coding Harness | Phase 2: Real Repository Tools",
        f"Status:       {snapshot['status']}",
        f"Run ID:       {snapshot['run_id']}",
        "Credential:   supplied via AI_API_KEY (presence/format validated only)",
        f"Workspace:    {snapshot['workspace'] or 'Not supplied (never defaults to the harness)'}",
        f"Task:         {preview}",
        f"Model family: {model['family'] or 'Not configured'}",
        f"Model ID:     {model['model_id'] or 'Not configured'}",
        f"Model calls:  {usage['model_calls']} | Tool calls: {usage['tool_calls']} | "
        f"Target checks: {usage['test_executions']} | Retries: {usage['recovery_attempts']}",
        f"Verification: {snapshot['verification_status']}",
        f"State file:   {snapshot['artifacts']['state_file']}",
        f"Event log:    {snapshot['artifacts']['events_file']}",
    ]
    if snapshot["missing_inputs"]:
        lines.extend([
            "Missing input: " + ", ".join(snapshot["missing_inputs"]),
            "Supply --workspace and --task/--task-file, or set HARNESS_WORKSPACE and HARNESS_TASK_FILE.",
            "Non-interactive startup finishes here; it does not stay running or process later messages.",
        ])
    lines.append(snapshot["note"])
    for line in lines:
        print(_terminal_text(line), file=output)


def main(
    argv: list[str] | None = None, *, env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None,
    project_root: Path | None = None,
) -> int:
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    redactor = Redactor(env.get("AI_API_KEY"))
    try:
        args = build_parser().parse_args(argv)
        if len(args.tool_args.encode("utf-8")) > 128000:
            raise ConfigurationError("Tool argument JSON exceeds 128000 bytes")
        try:
            tool_arguments = json.loads(args.tool_args)
        except (ValueError, RecursionError) as exc:
            raise ConfigurationError("--tool-args must be valid JSON") from exc
        if not isinstance(tool_arguments, dict) or (args.tool is None and tool_arguments):
            raise ConfigurationError("--tool-args requires a tool and a JSON object")
        overrides = {
            "workspace": args.workspace, "output_dir": args.output_dir, "log_level": args.log_level,
            "model": {"family": args.model_family, "model_id": args.model_id, "endpoint": args.endpoint,
                      "request_format": args.request_format, "response_format": args.response_format},
            "budgets": {item.name: getattr(args, item.name) for item in fields(BudgetConfig)},
        }
        result = initialize(
            StartupOptions(config_path=args.config, overrides=overrides, task=args.task,
                           task_file=args.task_file, task_stdin=args.task_stdin,
                           non_interactive=args.non_interactive, require_input=args.require_input),
            env=env, stdin=stdin, prompt_output=stderr,
            project_root=PROJECT_ROOT if project_root is None else project_root, cwd=Path.cwd(),
        )
        exit_code = 0
        if args.tool:
            from .tool_session import execute_tool
            exit_code = execute_tool(result, args.tool, tool_arguments, redactor)
        if args.json or args.tool:
            print(json.dumps(result.snapshot, ensure_ascii=False, indent=2, allow_nan=False), file=stdout)
        else:
            _display(result.snapshot, stdout)
        return exit_code
    except KeyboardInterrupt:
        payload = {"event": "startup.interrupted", "status": "INCOMPLETE", "error": "Interrupted during startup"}
        print(json.dumps(payload), file=stderr)
        return 130
    except (HarnessError, OSError, ValueError, RuntimeError) as exc:
        status = "BUDGET_EXHAUSTED" if isinstance(exc, BudgetExceeded) else "BLOCKED"
        error = str(exc) if isinstance(exc, HarnessError) else "Unable to initialize startup resources; check paths and permissions"
        print(json.dumps(redactor.clean({"event": "startup.failed", "status": status,
                                        "error_type": type(exc).__name__, "error": error})), file=stderr)
        return 2
