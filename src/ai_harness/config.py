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
    provider: str | None = None
    temperature: float | None = None
    max_tokens: int = 2048
    timeout_seconds: float | None = None
    max_retries: int | None = None
    retry_backoff_seconds: float = 0.25
    max_response_bytes: int = 1048576
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    request_template: dict[str, Any] | None = None
    response_mapping: dict[str, str] | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)

    @property
    def selected_provider(self) -> str | None:
        return self.provider or self.family


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
        for name in ("request_template", "response_mapping", "extra_body"):
            model[name + "_configured"] = bool(model.pop(name))
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
    "AI_RESPONSE_FORMAT": "response_format", "AI_PROVIDER": "provider",
    "AI_TEMPERATURE": "temperature", "AI_MAX_TOKENS": "max_tokens",
    "AI_TIMEOUT_SECONDS": "timeout_seconds", "AI_MAX_RETRIES": "max_retries",
    "AI_RETRY_BACKOFF_SECONDS": "retry_backoff_seconds",
    "AI_MAX_RESPONSE_BYTES": "max_response_bytes",

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
    validate_model_config(cfg.model)


def validate_model_config(model: ModelConfig) -> None:
    string_fields = {"family", "provider", "model_id", "endpoint", "request_format", "response_format", "auth_header"}
    for name in string_fields:
        value = getattr(model, name)
        if value is not None and (not isinstance(value, str) or not value.strip()
                or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ConfigurationError(f"model.{name} must be a bounded nonempty single-line string")
    if model.family not in {None, "deepseek", "qwen"} or model.provider not in {None, "deepseek", "qwen"}:
        raise ConfigurationError("model.provider/family must be deepseek or qwen when supplied")
    if model.provider and model.family and model.provider != model.family:
        raise ConfigurationError("model.provider and legacy model.family conflict")
    for name in ("temperature", "max_tokens", "timeout_seconds", "max_retries", "retry_backoff_seconds", "max_response_bytes"):
        value = getattr(model, name)
        if value is None and name in {"temperature", "timeout_seconds", "max_retries"}:
            continue
        integer = name in {"max_tokens", "max_retries", "max_response_bytes"}
        try:
            valid = not isinstance(value, bool) and isinstance(value, int if integer else (int, float)) and math.isfinite(value)
        except (TypeError, OverflowError):
            valid = False
        if not valid or value < 0 or (name in {"max_tokens", "timeout_seconds", "max_response_bytes"} and value == 0):
            raise ConfigurationError(f"model.{name} has an invalid numeric value")
    if model.max_retries is not None and model.max_retries > 20:
        raise ConfigurationError("model.max_retries must not exceed 20")
    if model.max_response_bytes > 16777216 or model.retry_backoff_seconds > 60:
        raise ConfigurationError("Model response/backoff limit is too large")
    if (not isinstance(model.auth_header, str) or not model.auth_header.isascii()
            or not all(c.isalnum() or c == '-' for c in model.auth_header)
            or model.auth_header.lower() in {"host", "content-length", "content-type", "connection", "transfer-encoding", "proxy-authorization"}):
        raise ConfigurationError("model.auth_header is not an allowed credential header")
    if (not isinstance(model.auth_scheme, str) or len(model.auth_scheme) > 32
            or not all(c.isascii() and (c.isalnum() or c in '-_') for c in model.auth_scheme)):
        raise ConfigurationError("model.auth_scheme must be empty or a short authentication scheme")
    # These are trusted operator data, not executable templates or credential storage.
    import json
    forbidden = {"api_key", "ai_api_key", "authorization", "password", "secret", "access_token", "credential"}
    def inspect(value: Any, depth: int = 0) -> None:
        if depth > 24:
            raise ConfigurationError("Model JSON configuration is nested too deeply")
        if isinstance(value, dict):
            if any(not isinstance(k, str) or k.lower() in forbidden for k in value):
                raise ConfigurationError("Credentials are forbidden in model JSON configuration")
            for child in value.values():
                inspect(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                inspect(child, depth + 1)
        elif value is not None and type(value) not in {str, int, float, bool}:
            raise ConfigurationError("Model configuration must contain JSON data only")
    for name in ("request_template", "response_mapping", "extra_body"):
        value = getattr(model, name)
        if value is None and name != "extra_body":
            continue
        if not isinstance(value, dict):
            raise ConfigurationError(f"model.{name} must be an object")
        inspect(value)
        try:
            if len(json.dumps(value, allow_nan=False).encode('utf-8')) > 96000:
                raise ValueError
        except (ValueError, TypeError, RecursionError, OverflowError) as exc:
            raise ConfigurationError("Model JSON configuration is invalid or too large") from exc
    if model.response_mapping is not None and any(not isinstance(v, str) for v in model.response_mapping.values()):
        raise ConfigurationError("model.response_mapping values must be JSON pointers")
    if model.endpoint:
        try:
            endpoint = urlsplit(model.endpoint)
            _ = endpoint.port  # Validate the port instead of deferring errors.
            allowed_http = endpoint.scheme == "http" and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
            if (not endpoint.hostname or endpoint.username is not None or endpoint.password is not None
                    or endpoint.query or endpoint.fragment or any(c.isspace() for c in model.endpoint)
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
    # Alias precedence follows the existing file < env < CLI contract.
    if "AI_PROVIDER" in env or "AI_MODEL_FAMILY" in env:
        model_data.pop("provider", None)
        model_data.pop("family", None)
    numeric_model = {"temperature": float, "max_tokens": int, "timeout_seconds": float,
                     "max_retries": int, "retry_backoff_seconds": float, "max_response_bytes": int}
    for name, setting in MODEL_ENV.items():
        if name in env:
            try:
                model_data[setting] = numeric_model.get(setting, str)(env[name])
            except (ValueError, TypeError, OverflowError) as exc:
                raise ConfigurationError(f"{name} has an invalid value") from exc
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
        if section == "model" and any(values.get(key) is not None for key in ("provider", "family")):
            target.pop("provider", None)
            target.pop("family", None)
        target.update({key: value for key, value in values.items() if value is not None})
    cfg = AppConfig(workspace=workspace, output_dir=output_dir, model=ModelConfig(**model_data),
                    budgets=BudgetConfig(**budget_data), log_level=level, config_file=selected,
                    tools=ToolLimits(**tool_data, command_timeout_seconds=budget_data.get("command_timeout_seconds", 60.0)),
                    checks=checks)
    validate_config(cfg)
    if selected is not None and workspace is not None and selected.is_relative_to(workspace.resolve()):
        raise ConfigurationError("Trusted harness configuration must be outside the target workspace")
    return cfg
