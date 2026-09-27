# AI Coding Harness — Phase 3

**Model adapters integrated into the existing Phase 1 + Phase 2 project.** Version
0.3.0 adds a generic model interface, explicit real DeepSeek/Qwen HTTP adapters,
and a development-only `MockModelAdapter`. The six repository tools and their
isolation implementation are unchanged. No earlier full-harness implementation
was imported, and no parallel application was created.

The working baseline is the exact Phase 2 archive. This release changes only
integration/configuration/state surfaces and adds the model modules. The full
patch and file-level continuity record identify every change.

**No official organizer endpoint, model ID or wire format has been supplied.**
Nothing is guessed. Real adapters require explicit configuration and `AI_API_KEY`.
Live DeepSeek/Qwen compatibility and evaluation performance are **not tested**.
The recorded HTTP tests use a local protocol fixture, not a provider service.

The default startup and all six direct `--tool` commands remain available.
`--model-step` explicitly performs one model request and at most one guarded tool
action. It is not a complete autonomous repair loop or final verifier.

## Start

```bash
make setup
make test
make demo-tools
make demo-model
```

Python 3.11+ and a POSIX environment are required for the file tools. **The complete
Phase 2 execution and Git-inspection path requires Linux with user, mount, PID,
IPC, UTS, and network namespaces enabled, util-linux `unshare`, system
`/usr/bin/python3`, and `/usr/bin/git`.** No root privileges are deliberately
requested: `unshare --user --map-root-user` creates a user namespace. Some Linux
hosts, containers, and distributions disable this facility; execution then
fails closed. There is no unrestricted local fallback. macOS needs a suitable
Linux VM for isolated checks; use WSL2/a Linux VM on Windows. Native macOS and
Windows execution were not tested.

Setup creates the harness `.venv` offline using Python's standard library.
It does not install system packages or download target dependencies. The target
runtime is the read-only system runtime **inside the sandbox**, not the harness's
virtual environment. Provision target dependencies in the evaluation Linux
runtime; an external target virtualenv is not mounted automatically.

The default `make run` still initializes the application without running target
code. In a terminal it can ask for missing task/workspace input. In a noninteractive
session it reports missing input honestly; `--require-input` makes that an error.

## Run one real tool from the CLI

The harness entry point requires `AI_API_KEY` from the environment, as in Phase 1.
No model API request is made by a direct `--tool` operation or ordinary startup. Never put a credential in
source code, an argument, or a committed configuration file.

With `AI_API_KEY` already supplied in the environment:

```bash
make run ARGS='--workspace /absolute/path/to/target --task "Inspect the issue" --tool list_files'

make run ARGS='--workspace /absolute/path/to/target --task "Inspect the issue" --tool search_code --tool-args '\''{"query":"average","pattern":"*.py"}'\'''

make run ARGS='--workspace /absolute/path/to/target --task "Inspect the issue" --tool get_changes'
```

For complex JSON, direct invocation after setup avoids a second layer of Makefile
shell quoting:

```bash
.venv/bin/python -I -m ai_harness \
  --workspace /absolute/path/to/target \
  --task "Fix the empty-input case" \
  --tool apply_patch \
  --tool-args '{"edits":[{"path":"calculator.py","old":"return sum(numbers) / len(numbers)","new":"return 0 if not numbers else sum(numbers) / len(numbers)"}]}'
```

**The target must be a separate, exclusively owned disposable directory.** It must
not be the harness, inside the harness, or an ancestor containing the harness.
Paths supplied to file tools are workspace-relative POSIX paths, not host paths.

## Configure checks

Checks are selected by name from trusted operator configuration outside the target.
A tool request cannot add executable arguments, choose a shell, or install packages.
No tests are guessed or automatically marked passed.

`harness.checks.example.toml` supplies a Python unittest example:

```toml
[[checks]]
name = "unit"
argv = ["{python}", "-m", "unittest", "discover", "-s", "tests", "-v"]
timeout_seconds = 20.0
```

Run that named check:

```bash
make run ARGS='--config harness.checks.example.toml --workspace /absolute/path/to/target --task "Check the change" --tool run_checks'
```

`{python}` expands to `/usr/bin/python3` inside the sandbox. Other executables must
be absolute system-bin paths. Shell/privilege-wrapper executables are rejected.
This is still trusted operator configuration: permitting an interpreter's `-c`
option does not make the supplied program trustworthy. The isolation layer, not
argument spelling alone, restricts the running program.

A check result contains actual `stdout`, `stderr`, `exit_code`, duration,
`command_started`, `timed_out`, `output_limit_exceeded`, and an execution error
when applicable. A failed, blocked, or timed-out check is never a pass. The direct
CLI exits 1 for completed check requests with failing results, 2 for rejected or
blocked requests, and 0 for successful tool requests. GNU Make wraps child failures
in its own nonzero status.

**`all_passed` means only that the selected commands actually started and returned
zero without a timeout/output-limit/launcher failure.** A successful command that
runs zero tests is not recognized as meaningful task verification in this phase.
`verification_status` remains `NOT_ASSESSED`; no `VERIFIED` task result is produced.

## What the six tools do

| Tool | Behavior |
|---|---|
| `list_files` | Bounded recursive listing, optional glob, skipped-path explanations; no link following. |
| `search_code` | Bounded literal text search with file names and one-based line numbers; optional case-insensitivity and glob. |
| `read_file` | Exact UTF-8 text/line range, SHA-256 of actual original bytes, explicit output truncation. |
| `apply_patch` | Validated exact replacements, new files and digest-guarded deletes; dry-run support and per-file atomic replacement. |
| `run_checks` | Real named commands in a Linux namespace sandbox; bounded pipes, timeouts, process cleanup, clean environment. |
| `get_changes` | Actual Git porcelain status, staged/unstaged diffs, and separate actual untracked-file diffs. |

See `docs/PHASE2_TOOLS.md` for signatures, patch rules, limits, and detailed
security boundaries. `docs/PHASE1_REPORT.md` and the pre-existing evidence files
are historical Phase 1 records, not claims about the current version.

## Isolation and deliberate limits

File operations use directory descriptors and `O_NOFOLLOW`, reject absolute/parent
paths, hardlinks, special files, credential paths, and Git metadata edits.

Check execution adds a user/mount/PID/network/IPC/UTS namespace and a reduced
filesystem. The sole writable **host-backed** mount is `/workspace`; `/tmp` is
private, size-limited memory scratch. System `/usr`, `/bin`, `/sbin`, and library
roots are explicitly readable, read-only runtime dependencies. Host home, the
harness directory, host `/proc`, and arbitrary host data directories are not
mounted. Credentials are not copied into the child environment. Existing usual
credential paths and `.git` metadata are masked for checks. Git inspection mounts
the target read-only and masks repository-local Git configuration.

This is not a claim of complete hostile-code safety: read-only runtime trees are
visible; kernel vulnerabilities, concurrent privileged host filesystem changes,
and aggregate fork/disk/CPU denial of service are not fully addressed. Use an
isolated evaluation machine/container with no secrets in its runtime tree for
untrusted repositories. Timeouts, private PID namespaces, output limits, and
per-process limits reduce risk but do not replace cgroup-wide quotas.

For conservative safety, execution refuses repositories containing symlinks,
hardlinks, sockets, devices, FIFOs or nested mounts. `get_changes` supports a
standalone SHA-1 Git checkout, not linked worktrees, alternate object stores,
shallow repositories, or submodule inspection. UTF-8 text editing only; no fuzzy
patching, rename operation, arbitrary shell tool, or automatic dependency install.

## Development demonstration

`make demo-tools` needs no API key. It copies the intentionally broken fixture to
a separate temporary Git repository and executes all six tools. Its real check
sequence is baseline failure → incorrect patch failure → repaired-code success.
It retains the target and writes `demo_report.json`; printed paths identify both.
**The decisions are scripted in this development demonstration. The edits, Git
commands, and test processes are real. No mock model exists in the production path.**

## Model adapter use

The offline model demonstration uses no API key:

```bash
make demo-model
```

It copies the **existing** average-function fixture into a separate temporary
repository. A baseline command actually fails, then the mock requests
`read_file → apply_patch → run_checks`. The existing tools do the work. Its report
contains actual command results and Git changes, with `is_mock=true` and zero
external model API calls. This tests integration, not model ability.

For a real call, have the organizer's credential already in `AI_API_KEY`, then set
`AI_PROVIDER`, `AI_MODEL_ID`, `AI_API_ENDPOINT`, `AI_REQUEST_FORMAT` and
`AI_RESPONSE_FORMAT` to confirmed values. The endpoint is the complete POST URL;
the adapter appends **nothing**. Only select `chat_completions`/`chat_json` or
`chat_tools` when the endpoint supports that chosen format. An alternative
`json_template`/`mapped_json` profile is configurable in trusted TOML.

```bash
# Configuration-only readiness. Does NOT claim the provider is reachable.
make run ARGS='--non-interactive --model-health'

# Explicit network probe; may incur provider usage, never executes tools.
make run ARGS='--non-interactive --model-health --live-health'

# One real model decision plus its validated tool action, not a complete loop.
make run ARGS='--workspace /absolute/path/to/target --task "Inspect the issue" --model-step'
```

A model-step run writes `model-events.jsonl`, `model_result.json`, and the updated
`run_state.json` in the existing run directory. Malformed responses become
structured `MODEL_ERROR` outcomes, not uncaught tracebacks or fabricated success.
Failed real checks return `CHECKS_FAILED`. None of these statuses is `VERIFIED`.
A normal text response is `MESSAGE`, not proof that the task is solved.

`model.provider`/`AI_PROVIDER` is the new provider setting; existing
`model.family`/`AI_MODEL_FAMILY` remains a supported alias. Model settings can be
provided through the existing defaults → TOML → environment → CLI precedence.
`AI_TEMPERATURE`, `AI_MAX_TOKENS`, `AI_TIMEOUT_SECONDS` and `AI_MAX_RETRIES` are
supported. Existing global budgets still cap per-call limits.

See `docs/PHASE3_MODEL_ADAPTER.md` for the interface, exact formats, examples,
health semantics, retry rules and limitations. `harness.model.example.toml` is an
explicit opt-in example, **not organizer-supplied configuration**.

## Reports and source map

A CLI tool run writes startup `events.jsonl`, `tool-events.jsonl`,
`run_state.json`, and `tool_result.json` under a private `.runs/<run-id>/`.
Budgets and actual tool/check counts are recorded. Direct tool runs keep model usage at zero.
Output redaction protects the environment-supplied credential where recognized,
but is not a general secret scanner.

```text
src/ai_harness/
  tools.py             Six-tool API, dispatcher, counters and event hooks
  tool_types.py        Validated check/patch/limit/result types
  repository_io.py     Descriptor-relative read/traversal guards
  patching.py          Exact validation, staging, publication and rollback
  execution.py         Real subprocess launch, bounded capture and cleanup
  _sandbox.py          Private namespace setup and reduced filesystem
  git_tools.py         Actual Git status/diff inspection
  tool_session.py      CLI tools connected to Phase 1 startup/state/logging
  model.py             Generic ModelAdapter protocol (controller dependency)
  model_types.py       Normalized requests/responses, usage, controlled errors
  model_providers.py   DeepSeek/Qwen adapters and production-only factory
  model_protocols.py   Explicit chat or configured JSON template codecs
  model_transport.py   Bounded actual HTTP requests, verified TLS, no redirects
  mock_model.py        Explicit scripted test adapter, never factory-selected
  model_tools.py       Schemas for the existing six tool signatures
  controller.py        One model-independent request/action integration step
  model_session.py     CLI composition, artifacts and health checks
  cli.py, config.py    Existing interface extended with model settings
  startup.py, ...     Preserved foundation components
```

`make clean` removes the development environment and caches, not target
repositories, source, or recorded run evidence.
