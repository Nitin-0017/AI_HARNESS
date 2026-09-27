"""Typed, strict TOML configuration: defaults < file < environment < CLI.

Config paths are anchored to the config file. Environment/CLI paths are anchored
at invocation time. Credentials are intentionally absent from all config types.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
import math
import os
from pathlib import Path
import tomllib
from typing import Any
from urllib.parse import urlsplit

from .errors import ConfigurationError
from .tool_types import CheckSpec, ToolLimits


@dataclass(frozen=True)
class ModelConfig:
    family: str | None = None
    model_id: str | None = None
    endpoint: str | None = None
    request_format: str | None = None
    response_format: str | None = None


@dataclass(frozen=True)
class BudgetConfig:
    max_seconds: float = 300.0
    max_iterations: int = 30
    max_model_calls: int = 30
    max_tool_calls: int = 100
    max_test_executions: int = 20
    max_total_tokens: int = 100000
    max_context_chars: int = 64000
    max_retries: int = 2
    command_timeout_seconds: float = 60.0
    model_timeout_seconds: float = 60.0
    max_task_bytes: int = 64000


@dataclass(frozen=True)
class AppConfig:
    workspace: Path | None
    output_dir: Path
    model: ModelConfig = field(default_factory=ModelConfig)
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    log_level: str = "INFO"
    config_file: Path | None = None
    tools: ToolLimits = field(default_factory=ToolLimits)
    checks: tuple[CheckSpec, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        model = asdict(self.model)
        model.pop("endpoint")
        model["endpoint_configured"] = bool(self.model.endpoint)
        return {
            "workspace": str(self.workspace) if self.workspace else None,
            "output_dir": str(self.output_dir),
            "model": model,
            "budgets": asdict(self.budgets),
            "tools": asdict(self.tools),
            "checks": [asdict(spec) for spec in self.checks],
            "log_level": self.log_level,
            "config_file": str(self.config_file) if self.config_file else None,
        }


FLOAT_BUDGETS = {"max_seconds", "command_timeout_seconds", "model_timeout_seconds"}
MODEL_ENV = {
    "AI_MODEL_FAMILY": "family", "AI_MODEL_ID": "model_id",
    "AI_API_ENDPOINT": "endpoint", "AI_REQUEST_FORMAT": "request_format",
    "AI_RESPONSE_FORMAT": "response_format",
}


def _path(value: Any, base: Path, name: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip() or "\0" in str(value):
        raise ConfigurationError(f"{name} must be a nonempty filesystem path")
    try:
        path = Path(value).expanduser()
        # Do not resolve symlinks here; workspace/output validation checks them.
        return path if path.is_absolute() else base / path
    except (ValueError, RuntimeError, OSError) as exc:
        raise ConfigurationError(f"Invalid path in {name}") from exc


def _table(data: dict[str, Any], name: str, allowed: set[str]) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"[{name}] must be a TOML table")
    if set(value) - allowed:
        raise ConfigurationError(f"Unknown setting in [{name}]; check harness.toml for supported names")
    return dict(value)


def validate_config(cfg: AppConfig) -> None:
    for item in fields(BudgetConfig):
        value = getattr(cfg.budgets, item.name)
        numeric = (int, float) if item.name in FLOAT_BUDGETS else (int,)
        if isinstance(value, bool) or not isinstance(value, numeric):
            raise ConfigurationError(f"budgets.{item.name} has the wrong numeric type")
        try:
            finite = math.isfinite(value)
        except (OverflowError, TypeError):
            finite = False
        zero_allowed = item.name == "max_retries"
        if not finite or value < 0 or (not zero_allowed and value == 0):
            requirement = "nonnegative" if zero_allowed else "positive"
            raise ConfigurationError(f"budgets.{item.name} must be finite and {requirement}")
    if not isinstance(cfg.log_level, str) or cfg.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigurationError("logging.level must be DEBUG, INFO, WARNING, or ERROR")
    for item in fields(ModelConfig):
        value = getattr(cfg.model, item.name)
        if value is not None and (
            not isinstance(value, str) or not value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ConfigurationError(f"model.{item.name} must be a nonempty single-line string")
    if cfg.model.family not in {None, "deepseek", "qwen"}:
        raise ConfigurationError("model.family must be deepseek or qwen when supplied")
    if cfg.model.endpoint:
        try:
            endpoint = urlsplit(cfg.model.endpoint)
            _ = endpoint.port  # Validate the port instead of deferring errors.
            allowed_http = endpoint.scheme == "http" and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
            if (not endpoint.hostname or endpoint.username is not None or endpoint.password is not None
                    or endpoint.query or endpoint.fragment or any(c.isspace() for c in cfg.model.endpoint)
                    or not (endpoint.scheme == "https" or allowed_http)):
                raise ValueError
        except ValueError as exc:
            raise ConfigurationError(
                "model.endpoint must be HTTPS (or loopback HTTP), without credentials, query, or fragment"
            ) from exc


def load_config(
    project_root: Path, *, config_path: Path | None = None,
    env: Mapping[str, str] | None = None, overrides: Mapping[str, Any] | None = None,
    cwd: Path | None = None,
) -> AppConfig:
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd
    project_root = project_root.resolve()
    overrides = {} if overrides is None else overrides
    selected: Path | None = None
    if config_path is not None:
        selected = _path(config_path, cwd, "--config")
    elif "HARNESS_CONFIG" in env:
        selected = _path(env["HARNESS_CONFIG"], cwd, "HARNESS_CONFIG")
    elif (project_root / "harness.toml").exists():
        selected = project_root / "harness.toml"
    data: dict[str, Any] = {}
    if selected is not None:
        try:
            if not selected.is_file() or selected.stat().st_size > 128000:
                raise OSError
            with selected.open("rb") as stream:
                data = tomllib.load(stream)
            selected = selected.resolve(strict=True)
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            # TOML parser diagnostics may reproduce values; don't echo them.
            raise ConfigurationError("Cannot read configuration: expected a valid TOML file of at most 128000 bytes") from exc
    if set(data) - {"workspace", "logging", "model", "budgets", "tools", "checks"}:
        raise ConfigurationError("Unknown configuration section; credentials are not allowed in configuration")
    workspace_table = _table(data, "workspace", {"path"})
    logging_table = _table(data, "logging", {"output_dir", "level"})
    model_data = _table(data, "model", {f.name for f in fields(ModelConfig)})
    budget_data = _table(data, "budgets", {f.name for f in fields(BudgetConfig)})
    tool_data = _table(data, "tools", {f.name for f in fields(ToolLimits)} - {"command_timeout_seconds"})
    check_data = data.get("checks", [])
    if not isinstance(check_data, list) or len(check_data) > 32:
        raise ConfigurationError("checks must be a TOML array with at most 32 check tables")
    try:
        checks = tuple(CheckSpec(**value) for value in check_data if isinstance(value, dict))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("Unknown or missing check configuration field") from exc
    if len(checks) != len(check_data) or len({s.name for s in checks}) != len(checks):
        raise ConfigurationError("Check definitions must be tables with unique names")
    anchor = selected.parent if selected else project_root
    workspace = _path(workspace_table["path"], anchor, "workspace.path") if "path" in workspace_table else None
    output_dir = _path(logging_table.get("output_dir", ".runs"), anchor, "logging.output_dir")
    level = logging_table.get("level", "INFO")
    if "HARNESS_WORKSPACE" in env:
        workspace = _path(env["HARNESS_WORKSPACE"], cwd, "HARNESS_WORKSPACE")
    if "HARNESS_OUTPUT_DIR" in env:
        output_dir = _path(env["HARNESS_OUTPUT_DIR"], cwd, "HARNESS_OUTPUT_DIR")
    if "HARNESS_LOG_LEVEL" in env:
        level = env["HARNESS_LOG_LEVEL"]
    for name, setting in MODEL_ENV.items():
        if name in env:
            model_data[setting] = env[name]
    for item in fields(BudgetConfig):
        name = "HARNESS_" + item.name.upper()
        if name in env:
            try:
                conversion = float if item.name in FLOAT_BUDGETS else int
                budget_data[item.name] = conversion(env[name])
            except (ValueError, TypeError, OverflowError) as exc:
                raise ConfigurationError(f"{name} must contain a valid number") from exc
    supported = {"workspace", "output_dir", "log_level", "model", "budgets"}
    if set(overrides) - supported:
        raise ConfigurationError("Unsupported CLI configuration override")
    if overrides.get("workspace") is not None:
        workspace = _path(overrides["workspace"], cwd, "--workspace")
    if overrides.get("output_dir") is not None:
        output_dir = _path(overrides["output_dir"], cwd, "--output-dir")
    if overrides.get("log_level") is not None:
        level = overrides["log_level"]
    for section, target in (("model", model_data), ("budgets", budget_data)):
        values = overrides.get(section, {})
        allowed = {f.name for f in fields(ModelConfig if section == "model" else BudgetConfig)}
        if not isinstance(values, Mapping) or set(values) - allowed:
            raise ConfigurationError(f"Invalid {section} override")
        target.update({key: value for key, value in values.items() if value is not None})
    cfg = AppConfig(workspace=workspace, output_dir=output_dir, model=ModelConfig(**model_data),
                    budgets=BudgetConfig(**budget_data), log_level=level, config_file=selected,
                    tools=ToolLimits(**tool_data, command_timeout_seconds=budget_data.get("command_timeout_seconds", 60.0)),
                    checks=checks)
    validate_config(cfg)
    if selected is not None and workspace is not None and selected.is_relative_to(workspace.resolve()):
        raise ConfigurationError("Trusted harness configuration must be outside the target workspace")
    return cfg
