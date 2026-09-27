# AI Coding Harness

One Python harness, one selected DeepSeek or Qwen model per run, and real guarded
repository tools. It extends the original Phases 1–5; the controller, model
interface, context manager and namespace runner remain the same components.

```
task + separate repository → inspect/context → model action → guarded tools
                         → checks → failure feedback/repair → final verification/report
```

## Install and launch

Prerequisites: Python 3.11+, GNU Make, Git, Linux with enabled user/mount/PID/network
namespaces, util-linux `unshare`, and `/usr/bin/python3`. Runtime and tests use the
Python standard library. The supplied test runner does not download target
dependencies; required target packages must be provisioned in the sandbox's
read-only system runtime. Unsupported isolation fails closed, never falling back
to unrestricted local execution. File-only tools also support suitable POSIX hosts.

```bash
git clone https://github.com/Nitin-0017/AI_HARNESS.git
cd AI_HARNESS
make setup
make test
```

`make setup` creates `.venv`, registers `src/`, and verifies imports without
network downloads. It does not install operating-system packages or enable kernel
namespaces. Those are declared prerequisites, not hidden manual repair steps.

The evaluator provides `AI_API_KEY` through the environment. Do not write it in
source, Makefile, TOML, a committed `.env`, or command history. The application
never loads `.env` files or provider-specific alternative credential variables.

Configure these **organizer-confirmed** settings in the environment or trusted
TOML outside the target repository:

| Setting | Environment variable |
|---|---|
| Provider (`deepseek` or `qwen`) | `AI_PROVIDER` |
| Exact model ID | `AI_MODEL_ID` |
| Complete POST endpoint | `AI_API_ENDPOINT` |
| Explicit request profile | `AI_REQUEST_FORMAT` |
| Explicit response profile | `AI_RESPONSE_FORMAT` |
| Optional temperature | `AI_TEMPERATURE` |
| Maximum response tokens | `AI_MAX_TOKENS` |
| Per-request timeout | `AI_TIMEOUT_SECONDS` |
| Retry limit | `AI_MAX_RETRIES` |

No endpoint, model ID, or wire format is invented. Supported profiles are the
existing non-streaming chat/JSON and configured-template interfaces documented in
`docs/PHASE3_MODEL_ADAPTER.md`. An endpoint is a full URL; no suffix is appended.
HTTPS is required except explicit loopback HTTP for local protocol tests/serving.
The exact organizer API is still unconfirmed. Missing configuration is not a
successful live-model evaluation.

With the credential and complete model configuration supplied:

```bash
make run
```

An interactive terminal prompts for the existing target workspace and task text,
then starts the agent. For automation:

```bash
export HARNESS_WORKSPACE="/absolute/path/to/separate/target"
export HARNESS_TASK_FILE="/absolute/path/to/issue.txt"
make run
```

The target cannot be the harness, inside it, or an ancestor containing it. Use a
standalone Git checkout owned exclusively by the run. A URL is not automatically
cloned or fetched. Multi-line tasks are accepted with `--task-file` or `--task-stdin`.
The organizer's exact machine/task transport is not specified by the supplied
brief; these are this implementation's documented input interfaces.

A **fully configured default launch now runs the agent**. A missing connection
keeps startup diagnostic-only and never claims the task was executed. Explicit
modes preserve earlier interfaces:

```bash
make run ARGS='--startup --json'               # validation only, zero model calls
make run ARGS='--agent --non-interactive'      # agent with JSON state/report
make run ARGS='--model-step'                   # at most one model action
make run ARGS='--model-health'                 # configuration check, no network
make run ARGS='--model-health --live-health'   # actual billable probe
make run ARGS='--tool list_files'              # one real tool, no model
make run ARGS='--help'
```

## Coding and verification

The generic adapter returns a validated action and arguments, optional `reason`
and `expected_outcome`, finish reason and reported usage. Provider formats stay
inside the adapter. The model cannot execute a shell or set a success verdict.

The agent inspects a bounded, relevant source/test selection before deciding.
An existing file must have a current read receipt before edits; stale or unread
sections require a new read. Patches are exact, unique text replacements, not
fuzzy edits. Existing tests cannot be deleted or have assertions replaced; new
regression files and conservative additive test updates are supported. Some
legitimate restructurings therefore require operator review rather than a bypass.

The six original tools remain `list_files`, `search_code`, `read_file`,
`apply_patch`, `run_checks`, and `get_changes`. `finish` is only a controller
request for independent verification. Each action remains within call, iteration,
time and output limits. Repeated unchanged failures require another strategy or
terminate honestly.

Checks come from trusted `[[checks]]` configuration (`harness.checks.example.toml`)
or conservative Python test discovery. Example:

```toml
[[checks]]
name = "full"
argv = ["{python}", "-m", "unittest", "discover", "-s", "tests", "-v"]
required = true
scope = "broad"
timeout_seconds = 60

[[checks]]
name = "calculator"
argv = ["{python}", "-m", "unittest", "discover", "-s", "tests", "-p", "test_calculator.py", "-v"]
required = false
scope = "targeted"
paths = ["calculator.py", "tests/test_calculator.py"]
```

Tool callers select **check names**, not new commands. `{python}` is the sandbox
system interpreter. Automatic discovery examines bounded local Python syntax,
uses unittest or pytest as appropriate, and does not import target code in the
host. Independent unittest directories receive required checks so nested
non-package tests are not silently skipped. Unknown build systems need explicit
trusted checks. Missing pytest/dependencies are reported, not automatically
installed. No discovered/configured checks means no `VERIFIED` result.

Targeted checks may guide repairs, but all required broader checks must pass for
completion. Verification uses real started-command status, exit code, stdout,
stderr, test-run evidence, final Git diff, and a stable eligible-file snapshot.
Zero recognized tests, stale results, truncated final diffs, timeouts or missing
required checks cannot verify a task. Historical failures remain in the report
after a repair. Passing available checks is not proof of all task requirements,
hidden-test success, or official evaluation performance.

## Context, resources and security

Context uses explicit TASK, REPOSITORY, RELEVANT CODE, TESTS, RECENT ACTIONS,
CURRENT CHANGES, FAILURES, VERIFICATION and BUDGET sections. It selects snippets,
deduplicates observations, removes stale evidence after edits, and retains recent
failures within item/count/character/byte limits. A bounded local AST index offers
symbols, imports and heuristic test/source links; it is not a complete dependency
analysis. Repository text is untrusted data and cannot override harness rules.

Budgets are set in `[budgets]`, `HARNESS_*` environment variables or CLI options.
Precedence remains defaults → TOML → environment → CLI. `max_runtime_seconds` is
an alias of `max_seconds`; `max_test_runs` aliases `max_test_executions`.
Use `--help` for all limits. Model/tool/read/search/edit/check counts, wall/model/
tool/check time, context size and recovery attempts are recorded. Exact input
counts are used only when the adapter supplies an exact counter. Otherwise
characters, UTF-8 bytes and conservative token reservations are explicitly
labeled estimates; unavailable complete billing totals remain null in reports.

Read/search caches reuse **previous actual results**, marked as cached, only
while guarded filesystem metadata is unchanged. Edits/checks invalidate them.
Duplicate successful checks may reuse evidence only for the same source snapshot;
final verification never relies on model prose. Give each run an exclusive
checkout: hostile concurrent host writers are not supported.

Path traversal, symlinks, hardlinks, special files and credential-shaped paths
are rejected by the original descriptor-relative I/O layer. Check processes run
in private Linux namespaces with a restricted filesystem, clean environment,
network isolation, process/output/time/file limits and descendant cleanup.
The target is the only writable host-backed data tree; runtime directories are
read-only. No API credential is inherited. Known environment credential values,
common secret fields and private-key blocks are redacted before model input/logs.
Redaction cannot identify every arbitrary or obfuscated secret.

This is not an audited kernel-exploit sandbox and does not enforce aggregate
cgroup CPU/process/memory quotas. Use a dedicated disposable host for hostile
repositories. Linked/shallow Git worktrees, symlinked dependencies and files beyond
configured limits can be blocked. See `docs/PHASE2_TOOLS.md` for the original
runtime boundary, which remains in force.

## Reports and testing

Each run writes private, redacted state and events under `.runs/<run-id>/`.
Agent execution additionally produces `report.json`, `summary.md`, and the
existing `model_result.json`. Reports include the task/model, actual changed
files, every recorded check outcome, targeted/broad results, checks not run or
blocked, final snapshot verification, budgets, usage, runtime and limitations.
Statuses: VERIFIED, FAILED, INCOMPLETE, BLOCKED, BUDGET_EXHAUSTED. The terminal
summary shows earlier failures as well as later passing checks.

```bash
make test        # complete unit/integration/real sandbox fixture suite
make demo-tools  # explicit scripted tool demonstration, no API needed
make demo-model  # explicit mock decisions, actual edits and checks
make clean       # remove environment/caches; retain run evidence and targets
```

MockModelAdapter is for deterministic tests and demonstrations, never selectable
by the production factory. The suite includes ten clean coding tasks, adversarial
agent scenarios, both provider classes against local HTTP protocol fixtures,
security, bounded context/budget, failure recovery and evaluator-entry tests.
A mock or loopback HTTP success is not a live DeepSeek/Qwen quality result.

Historical Phase 1–3 documents/evidence remain for continuity. The Python package
version remains 0.3.0 for compatibility; it is not a phase-completion counter.
Final execution evidence and any unavailable external validation are reported
separately from implementation claims.
