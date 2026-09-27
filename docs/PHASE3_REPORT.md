# Phase 3 completion report

## Continuity

Implemented in the existing Phase 2 working directory, using the exact Phase 2
archive as its baseline. Mounted documentation was verified against the archive
before missing source files were restored; no existing file was overwritten by
that restoration. The earlier full-harness archive was not imported. No GitHub
branch, commit or pull request was changed in this phase.

The six real tools remain in `RepositoryTools.call(...)` with unchanged signatures.
The files implementing tools, patches, repository I/O, process execution, sandbox,
Git inspection, tool sessions, workspace guards, environment handling and task
input are byte-for-byte unchanged. See `evidence/phase3/validation.json`.

## Added files

- `src/ai_harness/model.py`: generic `ModelAdapter` Protocol.
- `src/ai_harness/model_types.py`: normalized types, strict JSON/schema validation and controlled errors.
- `src/ai_harness/model_providers.py`: real DeepSeek and Qwen adapters, production factory and health checks.
- `src/ai_harness/model_protocols.py`: explicitly selected chat or configurable JSON template/pointer codecs.
- `src/ai_harness/model_transport.py`: actual bounded HTTP POST with TLS checks, timeouts and redirect rejection.
- `src/ai_harness/mock_model.py`: scripted development adapter, unavailable to the production factory.
- `src/ai_harness/model_tools.py`: schemas describing the unchanged Phase 2 tool interface.
- `src/ai_harness/controller.py`: one provider-independent request/action step with error and budget handling.
- `src/ai_harness/model_session.py`: CLI composition and model-result/event artifacts.
- `scripts/phase3_demo.py`: explicit mock read → patch → checks demonstration using real tools.
- `tests/model_support.py` and four `tests/test_model_*.py` modules: protocol, mock, configuration and integration coverage.
- `harness.model.example.toml`, this report, the adapter contract guide and Phase 3 execution evidence.

## Extended files

`config.py` adds provider and generation settings while preserving legacy family
and configuration precedence. `cli.py` adds opt-in model steps and health probes;
existing startup/direct tool paths remain. `state.py` adds honest execution and
unknown-usage accounting; `startup.py` reports the current phase. `telemetry.py`
permits the new model event-log filename. Makefile adds only `demo-model`.

Package/version metadata, the setup marker, README, example environment settings,
commented model settings and the release manifest were updated. No runtime
third-party dependency was added.

Three existing test files have expected semantic adjustments only: the package
version assertion is now 0.3.0, and the two obsolete `model_execution ==
NOT_IMPLEMENTED` assertions become `NOT_RUN`. Model execution now exists, but it
still does not occur on default startup. All prior test methods remain; no tests
were disabled or weakened to hide failures.

## Actual test results

Targeted model tests were used during development. After integration, `make test`
ran the entire suite: **359 passed, 0 failures, 0 errors, 0 skips**. This retains
254 prior tests and adds 105 model tests. The recorded suite took 6.258 seconds on
Linux x86_64, Python 3.13.5. Other Python versions/operating systems were not tested.

`make setup`, `make test`, `make run`, `make demo-model` and the unchanged
`make demo-tools` all returned zero in the recorded runs. Startup used a random
runtime environment value, not an API credential; it performed zero model calls
and left its separate target unchanged. It does not prove API authentication.

Local HTTP fixtures exercised both real provider adapter classes: actual POST
requests, selected paths/headers/body fields, normalized JSON/native tool results,
custom templates/pointers, malformed bodies and calls, missing configuration,
missing credentials, authentication errors, redirects, rate limits, retries,
network errors, real timeouts/slow bodies, usage/redaction and live-probe semantics.
These are protocol tests, not calls to DeepSeek or Qwen production services.

The requested demonstration observed a real failing baseline, then used
MockModelAdapter → read_file → apply_patch → run_checks. Actual checks passed
all three fixture tests after the change, and Git returned the real diff. The
report labels the decisions as mock and records zero external model API calls.
No autonomous `VERIFIED` task verdict is produced.

## Known limits

The organizer's exact endpoint, model ID and request/response contract remain
unknown. Live DeepSeek/Qwen compatibility has not been tested. Formats are
explicitly selected; an absent/unsupported selection fails honestly rather than
falling back to a mock or an invented protocol.

Supported transport is synchronous, non-streaming JSON POST. Unknown billing is
recorded as unknown with separate estimates; estimates are not exact tokenizer
counts. Multiple simultaneous tool calls are rejected. Full provider reasoning
state/native conversation persistence and an autonomous repair loop are outside
this phase. Health readiness without a live probe does not claim connectivity.

All existing Phase 2 runtime/isolation limitations remain. The model layer adds
no unrestricted check runner, arbitrary shell tool or credential source. Its socket
deadline mechanism is not a hard real-time DNS/kernel cancellation guarantee.

## Exact next integration point

A later orchestration phase should repeatedly call the existing
`ModelStepController.step(messages)` with bounded context and failure feedback,
using the same adapter interface and `RepositoryTools` instance. It should not
replace these components. Final-state verification must still be added before any
result can legitimately be labeled `VERIFIED`.
