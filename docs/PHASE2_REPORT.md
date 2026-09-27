# Phase 2 completion report

## Baseline and scope

The GitHub `main` branch was read before implementation. Its commit was
`40575eea3f9fcc8e981d484aa9d912af19b5b4d7`, with tree
`5f3259bd1293e0ec8b590816891068ef531baf17`. A direct clone was unavailable because
this environment could not resolve GitHub's hostname. The attached Phase 1 ZIP
was extracted and its computed Git tree matched that published tree exactly.
Implementation proceeded incrementally from those identical bytes.

All 121 foundation tests passed before changes. The package is now version
0.2.0; the existing version assertion was updated accordingly. No existing tests
were removed or weakened to avoid failures.

## Created

- `tool_types.py`: tool errors, bounded limits, check definitions, patch and result types.
- `repository_io.py`: guarded, descriptor-relative reads, walking and execution preflight.
- `patching.py`: exact validation, staging, atomic per-file replacement and rollback.
- `execution.py`: real bounded subprocess execution with isolated launcher status.
- `_sandbox.py`: Linux namespace setup, explicit read-only runtime, no-new-privileges.
- `git_tools.py`: real porcelain status, staged/unstaged and untracked content diffs.
- `tools.py`: six-tool public API, strict dispatch, state counters and logging hooks.
- `tool_session.py`: one explicitly selected tool connected to CLI startup and artifacts.
- `harness.checks.example.toml`: trusted check-registry example, no credentials.
- New test modules, fixture builder, fixture source repositories and a real tool demo.
- Phase 2 API/security documentation and evidence.

## Extended

Makefile/setup marker and demo entry point; package metadata/version; CLI; typed
configuration; startup result; run-state fields; logging filename handling; README.
Existing environment and workspace foundation behavior remains covered by its
original tests. The original broader full-harness archive was not merged into
this phase.

## Validation actually executed

`make setup`, `make test`, `make run`, a CLI `read_file`, and `make clean` all
returned exit code 0 in the recorded validation. Startup and read-tool operations
left the separate target's contents and modification times unchanged.

**254 tests passed with zero failures, zero errors and zero skips.** The entire
suite also passed as UID 1000, exercising the Linux namespace runner without host
root privileges. The runtime was Linux with Python 3.13.5. No claim is made for
other operating-system/kernel configurations.

Coverage includes real file reads and searches; invalid/absolute/Windows/parent
paths; symlink, hardlink, FIFO and root-replacement rejection; patches and stale
hash rejection; create/delete; batch validation and injected I/O rollback;
actual syntax errors and repairs; nonzero subprocess exits; captured stdout and
stderr; timeouts; detached-descendant cleanup; output limits; isolated host-file
and network access; credential handling; actual Git status/diffs; registry/CLI
validation; and resource counters.

The manual-development fixture sequence ran every tool. It observed actual
baseline test failure, actual failure after an incorrect edit, and three passing
fixture tests after repair. The final diff was obtained from Git. The decisions
in that demonstration were scripted; no model made them. Production contains no
fake model fallback or fabricated command output.

A Git-inspection smoke test initially exposed a read-only bind-mount destination
being touched by the launcher. That implementation error was fixed before the
full suite. Further review added nested credential/metadata masking, strict type
validation and time-budget propagation. The complete suite was rerun after the
fixes; no known test failure is being deferred.

## Remaining boundaries

Checks and Git inspection require Linux namespace support, system Python and Git;
unsupported systems fail closed. File-only operations require POSIX descriptor
APIs. User-defined target virtualenvs and dependencies outside the explicit
read-only runtime are not automatically exposed. Conservative execution refuses
symlinks, hardlinks, special files and nested mounts. Git inspection is limited
to standalone SHA-1 checkouts, without shallow/linked/alternate configurations.

Read-only system runtime directories are visible by design. This is not an
independently audited defense against kernel exploits or aggregate resource
exhaustion. Concurrent privileged host writers are unsupported. Multi-file patch
publication is not a filesystem transaction. Output redaction is not a complete
secret scanner. See `PHASE2_TOOLS.md` for exact boundaries.

No agent controller, model adapter, automatic repair decisions or final-state
verification verdict has been added. `all_passed` reports the actual selected
command outcomes; it does not prove that a meaningful nonzero number of tests ran
or that the organizer's task is solved. A later verification phase must enforce
those rules before declaring `VERIFIED`.

## Next development boundary

Phase 3 can use these six tools to implement the bounded controller and
context/state flow. The approved model adapter and autonomous verification
logic remain separate later work. This phase stops after tool implementation
and successful validation.
