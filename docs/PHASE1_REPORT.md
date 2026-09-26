# Phase 1 implementation report

## Scope

Implemented a runnable, testable project foundation only. The previous
`ai-coding-harness.zip` was inspected and preserved unchanged. This separate
`ai-harness-phase1` package intentionally excludes the old package's later-phase
agent/model/tool execution. No GitHub change was made by this implementation step.

## Files created

| Files | Responsibility |
|---|---|
| `Makefile` | `setup`, `run`, `test`, and `clean` entry points. |
| `pyproject.toml`, `requirements.txt` | Python package metadata and zero third-party runtime/test dependencies. |
| `harness.toml` | Public, typed configuration defaults; no assumed model endpoint or target. |
| `.env.example`, `.gitignore` | Empty environment-variable reference and exclusions for secrets/artifacts. |
| `scripts/setup.py`, `scripts/clean.py` | Offline virtual-environment setup and narrowly scoped cleanup. |
| `src/ai_harness/__init__.py`, `__main__.py` | Package and runnable module entry. |
| `src/ai_harness/cli.py` | Text CLI, bounded input options, terminal display, redacted errors. |
| `src/ai_harness/config.py` | TOML/environment/CLI precedence and validated model/resource settings. |
| `src/ai_harness/environment.py` | Environment-only `AI_API_KEY` validation and protected credential representation. |
| `src/ai_harness/errors.py` | Explicit controlled exception classes. |
| `src/ai_harness/inputs.py` | Task text, UTF-8 file, stdin, and environment input. |
| `src/ai_harness/startup.py` | Foundation composition and startup flow; no model or tool executor. |
| `src/ai_harness/state.py` | Run ID, phase/status, task, counters, budgets, and monotonic timing. |
| `src/ai_harness/telemetry.py` | Redaction, JSON Lines events, and atomic state persistence. |
| `src/ai_harness/workspace.py` | Target/harness separation, canonical paths, and output isolation. |
| `tests/` | Configuration, environment, task input, workspace, state/logging, startup, subprocess CLI, and cleanup tests. |
| `README.md`, `docs/PHASE1_REPORT.md` | Usage, scope, defaults, limitations, and phase completion record. |
| `evidence/` | Actual command transcripts, startup state/events, and validation summary. |

## What was executed

The main validation completed on Linux with Python 3.13.5 and GNU Make 4.4.1.
These are observed development-environment versions, not organizer runtime claims.

| Command / check | Actual result |
|---|---|
| `make setup` | Exit 0. Source package imported successfully in `.venv`. No package downloads. |
| `make test` | Exit 0. **121 automated tests passed.** |
| `make run` with environment-provided task/workspace | Exit 0; saved `READY` foundation state. |
| `make run` with credential but no task/workspace, non-interactive | Exit 0; saved `AWAITING_INPUT`, not a completed task. |
| `make run` without `AI_API_KEY` | Expected nonzero result; controlled `BLOCKED` environment validation error. |
| Repeated `make setup` | Exit 0; idempotent setup. |
| `make clean` | Exit 0; source, existing startup logs, and target workspace preserved. |
| Setup and run after clean | Both exit 0. |
| Target before/after comparison | Contents, modification times, and inventory unchanged. |
| Original full-harness archive checksum | Unchanged. |

For startup command validation, a fresh random value was supplied through the
process environment only. It was not a real API credential and was not
hard-coded, committed, authenticated, or sent to any model. The validation
explicitly checked its absence from saved evidence.

The successful initialized state recorded **0 model calls, 0 tool calls, and
0 target-test executions**, with `verification_status: NOT_RUN`. The 121 tests
are tests of the harness foundation, not execution of checks in the target repo.

## Important behavior and limitations

`READY` means startup has accepted the inputs and saved state, not that a coding
task is solved. No network client, model adapter, agent loop, repository tool,
command runner, or task-verification engine is present. DeepSeek/Qwen family,
model ID, endpoint, and format fields are configuration for later integration.
The exact model/API details are still unknown; live compatibility is untested.

Task byte/context limits and startup elapsed-time checks are enforced. Other
resource limits are validated and recorded, but their consuming components do
not exist in this phase. There is no fabricated usage or model execution.

A missing API key deliberately blocks normal startup. `make setup`, `make test`,
and CLI help work without one. With a key and missing task/workspace, an
interactive terminal prompts; a non-interactive invocation writes an
`AWAITING_INPUT` snapshot and exits. It does not stay alive to process later input.

Target workspaces must be separate from the harness in both directions. Path
validation and symlink checks are not an OS/process sandbox or a defense against
all concurrent filesystem races. This phase executes no target code. Known-key
redaction does not identify arbitrary unrelated secrets in task text.

The Makefile path is the validated installation/entry path. Linux/Python 3.13.5
was executed; Python 3.11+, macOS, and WSL are intended targets but were not run
in this environment. This package is not yet a complete evaluation submission.

## Exact next implementation step

Add Phase 2 repository tools and their guards: file listing/search/reads, targeted
patch application, change inspection, and controlled check execution. Test actual
operations against temporary separate workspaces, including failure paths. Keep
model execution out until its adapter interface is implemented and tested.
