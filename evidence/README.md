# Recorded execution evidence

Files directly under this directory are preserved **historical Phase 1** records.
The current Phase 2 implementation and execution results are in `phase2/`.

Phase 2 includes complete harness-suite output, startup and read-tool transcripts,
cleanup results, a real tool demonstration, and an unprivileged Linux test run.
The demo is explicitly scripted and does not evaluate a language model. No
DeepSeek/Qwen endpoint was contacted or authenticated.

`phase2/summary.json` records the environment, actual command statuses, counts,
and source hashes. Runtime absolute paths inside transcripts are the real paths
used during validation; they are not paths that the evaluator must reproduce.
