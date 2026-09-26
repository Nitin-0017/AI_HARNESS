# Recorded execution evidence

These are actual command outputs captured while validating the Phase 1 source.
`validation.json` records return codes, durations, runtime versions, and checks.

- `setup.txt`, `setup-idempotent.txt`, `setup-after-clean.txt`: offline setup runs.
- `automated-tests.txt`: real unittest output (121 tests, all passed).
- `make-run.txt`: plain `make run` with task/workspace supplied in the environment.
- `make-run-awaiting-input.txt`: valid credential environment, missing task/workspace.
- `missing-environment.txt`: an intentional negative check with `AI_API_KEY` absent.
- `clean.txt`, `make-run-after-clean.txt`: cleanup and subsequent startup.
- `startup-state.json`, `startup-events.jsonl`: one real initialized foundation run.

A randomly generated temporary environment value was used only to test credential
presence/format. No real API key was supplied or authenticated. No model was called.
The task workspace was a temporary independent directory and was not changed.
Absolute paths/run IDs in these transcripts belong to that validation session;
they are evidence, not configuration to reuse on another computer.
