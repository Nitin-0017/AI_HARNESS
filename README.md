# AI Coding Harness — Phase 1: Project Foundation

A **real, runnable foundation**, not a simulated coding agent. This version loads
configuration, validates environment credentials, accepts a text task and an
explicitly separate target workspace, and writes structured startup state and
logs. **It does not call any model, edit target files, execute target tests, or
claim to solve a task.**

This is a separate Phase 1 package. The previously supplied full-harness ZIP was
left unchanged; its later-phase model/controller/tool implementations are not
included in this package.

## 1. Requirements and setup

Use Python **3.11 or newer**, GNU Make, and Linux, macOS, or WSL. The recorded
validation was performed on **Linux with Python 3.13.5**. Other operating systems
and Python versions were not executed during this delivery.

```bash
cd ai-harness-phase1
make setup
make test
```

`make setup` creates `.venv` and registers `src/` with that environment using a
`.pth` file. Runtime and tests use only the Python standard library: setup does
not call pip, download packages, require an API key, or contact a model. Re-running
setup is safe. `pyproject.toml` also declares standard packaging metadata; its
optional setuptools build path is not needed by `make setup`.

## 2. Supply the credential through the environment

The application reads **only `AI_API_KEY`**. It does not accept a credential
argument, parse credentials from TOML, read a `.env` file, or use a provider-specific
key as a fallback. `.env.example` is documentation with empty values, not a file
to source into your shell.

The evaluator can inject `AI_API_KEY` directly. For a local Bash session, enter it
without putting its value in a command or file:

```bash
read -r -s -p 'AI_API_KEY: ' AI_API_KEY
printf '\n'
export AI_API_KEY
```

Phase 1 checks presence and basic value format only. **It does not authenticate
the key or confirm that an API accepts it.** A missing or invalid value causes a
controlled nonzero startup result. `make setup`, `make test`, and `--help` do not
need a real credential.

## 3. Start the foundation

With `AI_API_KEY` already set:

```bash
make run
```

In an interactive terminal, missing workspace/task inputs are prompted for. The
credential is never prompted for inside the harness. In a non-interactive session,
missing task/workspace inputs produce `AWAITING_INPUT`, a saved state, and exit 0.
**This is a completed foundation startup, not an active background process.** It
does not remain running to receive subsequent messages. Supply inputs and rerun.

To initialize a concrete task without editing source or configuration files:

```bash
export HARNESS_WORKSPACE="/absolute/path/to/separate/target-repository"
export HARNESS_TASK_FILE="/absolute/path/to/issue.txt"
make run
```

Or use explicit CLI input:

```bash
make run ARGS='--workspace /absolute/path/to/separate/target --task "Inspect the empty-input behavior" --require-input'

make run ARGS='--repo /absolute/path/to/separate/target --task-file /absolute/path/to/issue.txt --require-input --json'

printf '%s\n' 'Inspect the empty-input behavior' | \
  make run ARGS='--workspace /absolute/path/to/separate/target --task-stdin --require-input --json'
```

`--repo` is an alias for `--workspace`. `--require-input` changes missing input
from `AWAITING_INPUT` into an error. `--non-interactive` disables terminal prompts.
A URL in task text is just text; Phase 1 does not fetch issues or clone a repository.

### Startup statuses are not task-result statuses

| Status | Meaning | Python CLI exit |
|---|---|---:|
| `READY` | Credential, configuration, task, workspace, and output initialization succeeded. | 0 |
| `AWAITING_INPUT` | Foundation started, but task and/or workspace is missing. | 0 |
| `BLOCKED` | Invalid environment, configuration, inputs, workspace, or startup filesystem operation. | 2 |
| `BUDGET_EXHAUSTED` | The startup time budget was reached. | 2 |
| `INCOMPLETE` | Operator interrupted startup. | 130 |

The verification field is always `NOT_RUN`; `task_result` is always null. No
`VERIFIED` task result can be emitted by this version. GNU Make may map a child's
nonzero exit code to its own failure code. Use `.venv/bin/python -I -m ai_harness`
when the exact CLI exit code matters.

## 4. Configuration

Precedence: **built-in defaults < TOML file < environment < explicit CLI options**.

The default file is `harness.toml` in the harness project, **not a similarly named
file in the target/current directory**. `--config` overrides `HARNESS_CONFIG`.
Explicitly requested missing/invalid files fail rather than silently using defaults.
Unknown tables and settings are rejected, including credential fields.

Paths from TOML are relative to that file. Environment and CLI paths are relative
to the invocation directory; absolute workspace paths are recommended. No target
is inferred from the current directory.

| Setting | Environment variable | CLI |
|---|---|---|
| Credential | `AI_API_KEY` | Not accepted |
| Target workspace | `HARNESS_WORKSPACE` | `--workspace` / `--repo` |
| Task text | `HARNESS_TASK` | `--task` |
| Task file | `HARNESS_TASK_FILE` | `--task-file` |
| Config file | `HARNESS_CONFIG` | `--config` |
| Log root | `HARNESS_OUTPUT_DIR` | `--output-dir` |
| Log level | `HARNESS_LOG_LEVEL` | `--log-level` |
| Model family | `AI_MODEL_FAMILY` | `--model-family` |
| Exact model ID | `AI_MODEL_ID` | `--model-id` / `--model` |
| Exact endpoint | `AI_API_ENDPOINT` | `--endpoint` |
| Request format | `AI_REQUEST_FORMAT` | `--request-format` |
| Response format | `AI_RESPONSE_FORMAT` | `--response-format` |

Only `AI_API_KEY` comes from the supplied submission contract. The other names
and the task-input interface are implementation choices, not claims about the
organizers' final evaluation interface.

Do not set both `HARNESS_TASK` and `HARNESS_TASK_FILE`. An explicitly chosen CLI
task source overrides environment task sources.

Model settings are optional **reserved configuration**, not an implemented model
connection. Family may be `deepseek` or `qwen`; the exact ID, endpoint, and message
formats stay unset unless supplied. No provider is selected automatically, and no
network request is made. Endpoint validation checks URL shape only; HTTPS and
loopback HTTP are accepted, with URL credentials/query/fragment rejected. Live
API compatibility is outside Phase 1.

### Budgets

Each `[budgets]` field also has `HARNESS_<UPPERCASE_FIELD>` and
`--<hyphenated-field>` forms. For example:

```bash
export HARNESS_MAX_SECONDS=120
export HARNESS_MAX_MODEL_CALLS=12
make run ARGS='--max-context-chars 32000 --max-retries 1'
```

| Field | Default |
|---|---:|
| `max_seconds` | 300 |
| `max_iterations` | 30 |
| `max_model_calls` | 30 |
| `max_tool_calls` | 100 |
| `max_test_executions` | 20 |
| `max_total_tokens` | 100000 |
| `max_context_chars` | 64000 |
| `max_retries` | 2 |
| `command_timeout_seconds` | 60 |
| `model_timeout_seconds` | 60 |
| `max_task_bytes` | 64000 |

These are configurable development defaults, **not official organizer limits**.
Values are type/range checked; booleans, nonfinite values, and negative budgets
are rejected. `max_retries` permits zero. Task byte/context limits and the startup
wall-clock check are active. Model/tool/test/iteration/token/retry limits are
recorded for future components; there is no such execution loop to limit yet.
Time spent waiting for interactive operator input is excluded from runtime.

## 5. Workspace isolation and logs

The target must already exist and must be disjoint from the harness: it cannot
be the harness, a child inside it, or an ancestor containing it. A `.git` directory
is not required for this foundation. A symlinked target is canonicalized before
checking separation. The path guard rejects absolute tool paths, parent traversal,
and symlinks that escape the target. The guard only validates paths; it is not a
repository editing or command-execution tool.

Startup never changes the working directory, imports target code, runs target
commands, creates files in the target, or chooses the harness as a default target.
The Makefile runs Python in isolated mode (`-I`) to ignore `PYTHONPATH` and prevent
current-directory module shadowing in that entry path.

By default, each startup creates:

```text
.runs/<run-id>/
├── events.jsonl       # Structured, timestamped startup events
└── run_state.json     # Redacted, atomically written startup snapshot
```

Output directories must not overlap the target. Symlinked output components and
output inside harness code/environment directories are rejected. Run directories
and files use private POSIX permissions. State includes the task, selected model
metadata, budgets, missing inputs, and counters. It does not contain a credential
object; logs/output redact the supplied API key and named sensitive fields.

**Do not place unrelated secrets in task text.** Redaction of the known API key is
not a general guarantee that every possible secret can be recognized. Path checks
are not an OS sandbox and do not solve races caused by concurrent filesystem
changes. Process isolation belongs to a later phase; this version executes no
target code.

## 6. Project layout

```text
ai-harness-phase1/
├── Makefile
├── pyproject.toml
├── requirements.txt
├── harness.toml
├── .env.example
├── .gitignore
├── scripts/
│   ├── setup.py
│   └── clean.py
├── src/ai_harness/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── environment.py
│   ├── errors.py
│   ├── inputs.py
│   ├── startup.py
│   ├── state.py
│   ├── telemetry.py
│   └── workspace.py
├── tests/
├── docs/PHASE1_REPORT.md
└── evidence/
```

## 7. Testing and cleanup

```bash
make setup
make test
make run       # AI_API_KEY must already be supplied in the environment
make clean
```

Tests use temporary directories and dynamically generated validation values, not
real or embedded API credentials. They execute configuration/startup logic, file
operations for logging, and subprocess CLI checks. There is no mock-model
execution path. Model networking and target command execution are forbidden in
the corresponding tests. The suite also covers symlink escapes, output isolation,
Unicode input, byte limits, redaction, error exits, startup budgets, and cleanup.

`make clean` removes `.venv`, build artifacts, and caches while preserving source,
`.runs` evidence, and target workspaces. Setup recreates the local environment.
The ZIP excludes `.venv`, runtime `.runs`, and caches; always run setup after
extracting it. Recorded commands and real results are in `evidence/`.

## 8. Deliberately not implemented yet

No model adapter, network client, agent controller, repository search/edit tools,
command runner, test-selection engine, repair loop, or task-verification engine
is included. The CLI prepares and records a task; it does not solve it. This
Phase 1 package alone is **not the complete hackathon submission**.

The next phase is to add tested repository tools and guarded execution around
this foundation, without changing the environment/Makefile contract.
