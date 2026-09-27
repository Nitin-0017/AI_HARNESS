# Phase 3 — model adapter contract

## Incremental integration

The current Phase 2 `RepositoryTools.call(name, arguments)` API is unchanged.
No file/tool implementation was replaced. The new controller has only one
operation, `step(messages)`: call a generic adapter, validate its response, and
execute at most one requested action through the existing tools. It does not
import a provider or mock. The later autonomous loop can reuse this boundary.

Production composition happens in `model_session.py` and the factory in
`model_providers.py`. The factory accepts only `deepseek` or `qwen`, never `mock`.
Direct startup and Phase 2 tool commands remain non-model operations.

## Generic model interface

```python
class ModelAdapter(Protocol):
    def generate(self, request: ModelRequest, *, before_attempt=None) -> ModelResponse: ...
    def health_check(self, *, live=False, before_attempt=None, timeout_seconds=None) -> HealthCheck: ...
    def metadata(self) -> dict: ...
```

`ModelRequest` carries text messages, tool definitions and optional lower request
limits. `before_attempt` is invoked before every HTTP attempt, including retries,
so the controller's existing run budgets cannot be bypassed by retries. Budget
exceptions propagate to a controlled `BUDGET_EXHAUSTED` step. Adapters do not
execute tools themselves.

Normalized response:

```python
ModelResponse(
    message: str,
    requested_action: str | None,
    arguments: dict,
    finish_reason: str,  # stop, tool_call, length, refused, error, unknown
    usage: TokenUsage,  # input_tokens/output_tokens/total_tokens, each int | None
    raw_metadata: dict,
)
```

`raw_metadata` is a bounded projection of response identity and transport metadata,
not the raw response body. Full reasoning traces, headers and the credential are
not retained. Missing usage remains null, not a fabricated zero. Run-state token
counters contain reported amounts; `estimated_tokens` separately reserves a
UTF-8-size-based estimate plus output allowance when usage is unknown (including
failed attempts). This is not an exact tokenizer or provider billing meter.

### Validation

Response JSON must be valid UTF-8, bounded in bytes/depth, contain no duplicate
keys or nonfinite numbers, and have the selected shape. Responses are checked
before dispatch, including known action name, supported argument fields/types,
and coherent nonnegative usage. Existing tools still perform authoritative path,
patch and execution checks.

The controller catches controlled model and tool errors and returns a structured
outcome. A malformed response does not run an action, is not silently changed into
a valid action, and is not blindly retried. A later explicit step can supply the
error as feedback. The deterministic mock is subject to the same validation.

At most one complete action is accepted. Multiple choices/calls are rejected, not
silently truncated. Length-limited, refused, errored and unknown finishes cannot
execute an action. An ordinary assistant message is not a verification verdict.

## Provider configuration

No provider/model/endpoint/wire format is selected automatically. The final
organizer contract is still unknown. Both concrete adapters use the shared,
explicitly selected codec and actual HTTP transport.

| Setting | Environment | CLI | Default |
|---|---|---|---|
| `model.provider` | `AI_PROVIDER` | `--provider` | unset |
| legacy `model.family` | `AI_MODEL_FAMILY` | `--model-family` | unset |
| `model.model_id` | `AI_MODEL_ID` | `--model-id` | unset |
| `model.endpoint` | `AI_API_ENDPOINT` | `--endpoint` | unset |
| `model.request_format` | `AI_REQUEST_FORMAT` | `--request-format` | unset |
| `model.response_format` | `AI_RESPONSE_FORMAT` | `--response-format` | unset |
| `model.temperature` | `AI_TEMPERATURE` | `--temperature` | omitted |
| `model.max_tokens` | `AI_MAX_TOKENS` | `--max-tokens` | 2048 |
| `model.timeout_seconds` | `AI_TIMEOUT_SECONDS` | `--api-timeout` | existing model timeout budget |
| `model.max_retries` | `AI_MAX_RETRIES` | `--api-retries` | existing retry budget |
| `model.retry_backoff_seconds` | `AI_RETRY_BACKOFF_SECONDS` | TOML only | 0.25 |
| `model.max_response_bytes` | `AI_MAX_RESPONSE_BYTES` | TOML only | 1048576 |

The existing defaults → TOML → environment → CLI precedence is preserved. Provider
and legacy family refer to the same selection; contradictory values at the same
precedence level are rejected. Setting one at a higher level supersedes a lower
level alias. Global run budgets remain upper bounds on timeout/retry/call limits.

`AI_API_KEY` is read only through the existing environment loader. `.env.example`
is documentation, not a file to source wholesale; blank numeric environment
variables are invalid. No provider-specific alternate credential variable is read.

`endpoint` is the complete POST URL. No path suffix is added. HTTPS is required
except for explicit loopback HTTP used by local tests/serving. Embedded credentials,
query strings, fragments and non-loopback cleartext URLs are rejected.

By default authentication uses `Authorization: Bearer <environment value>`.
Trusted `model.auth_header` and `model.auth_scheme` can select a different header
name/scheme without editing controller logic; an empty scheme sends the value
alone. Header injection/framing overrides are rejected. The key cannot appear in
request templates/config files. Do not put credentials in any URL or custom data.

## Explicit wire formats

### `chat_completions` requests

Sends a non-streaming JSON POST with `model`, text `messages`, `max_tokens`, and
`stream:false`. `temperature` is included only if configured. Explicit
`model.extra_body` can add provider-supported options without changing the
controller. It cannot replace controlled model/messages/tools/limit fields.
No thinking-mode flags or particular model versions are inferred.

Select either response profile:

- **`chat_json`**: the assistant's content must be exactly a JSON object containing
  `message`, `requested_action` and `arguments`. The adapter adds that explicit
  instruction and tool descriptions to the request. This is our selected harness
  action contract, not a claim that an unknown organizer requires that format.
- **`chat_tools`**: send native function schemas and decode a single function call
  from the assistant's `tool_calls`, with JSON-string arguments and call ID. Plain
  assistant text is allowed when no action is requested. Parallel calls are not
  accepted, and there is no automatic fallback to JSON-content parsing.

These codec implementations are based on the providers' public chat API
references, but the chosen organizer endpoint still needs live compatibility
validation. A model may require explicit extra flags or not support these modes.

### `json_template` requests / `mapped_json` responses

For a different **non-streaming JSON POST** service, supply a trusted request
object and response field pointers in TOML. Placeholders are whole JSON values:
`{{model_id}}`, `{{messages}}`, `{{tools}}`, `{{temperature}}`, `{{max_tokens}}`.
They retain their types; no eval, executable templating or arbitrary file access
exists. Model ID, messages and output-token-limit placeholders are mandatory.

Illustration only — **not an asserted DeepSeek/Qwen/organizer schema**:

```toml
[model]
request_format = "json_template"
response_format = "mapped_json"

[model.request_template]
engine = "{{model_id}}"
conversation = "{{messages}}"
output_limit = "{{max_tokens}}"

[model.response_mapping]
message = "/result/text"
requested_action = "/result/action"
arguments = "/result/arguments"
finish_reason = "/result/finish"
# Optional, when actually present:
# input_tokens = "/usage/input"
# output_tokens = "/usage/output"
# total_tokens = "/usage/total"
```

Pointers follow slash-separated object/array navigation with `~0`/`~1` escapes.
Configured fields must exist; missing fields cause a controlled format error.
Unconfigured usage fields remain unknown. The endpoint, provider and model ID
must still be supplied explicitly. This changes the wire mapping without touching
the controller. Unsupported non-JSON/streaming/auth flows require a future codec
or transport extension, never silently guessed behavior.

## Real network behavior and health semantics

HTTP uses standard-library `http.client`, certificate/hostname verification,
bounded responses and watchdog-assisted timeouts. Redirects are rejected;
credentials are not forwarded to a redirect destination. Environment proxy
settings are not implicitly used. API errors never echo the provider body or key.

Only 408/429/500/502/503/504 and transient network/timeout failures are eligible
for bounded retries. Authentication/format errors are not retried. Retry means
additional attempts, not the total number. Backoff is bounded and constrained by
the request deadline and run budget. A lost response can mean a provider processed
and billed a request; retries do not guarantee exactly-once provider execution.

`health_check()` is local validation only:

- `BLOCKED`: missing/invalid configuration or credential; no successful model claim.
- `CONFIGURED_NOT_PROBED`: configuration is valid; network not checked; response_valid=null.
- Explicit `live=True`: sends one small generation request to the **same configured
  endpoint**, with no tools and no HTTP retries; it may incur cost.
- `REACHABLE`: that actual probe returned a valid completed response. It does not
  certify all model features or official evaluation performance.
- `UNAVAILABLE` / `INVALID_PROBE`: the actual probe failed or did not finish normally.
- Mock health is always `MOCK_ONLY`, never live success.

OS DNS lookup and pathological kernel/network stalls are not perfectly cancellable
in synchronous Python. The socket watchdog bounds tested header/body stalls;
this is not a hard real-time guarantee. A dedicated evaluation runtime should
apply an outer process deadline where required.

## CLI and results

`--model-step` and `--model-health` are mutually exclusive with `--tool`.
`--live-health` requires `--model-health`. Ordinary `make run` still does startup
only and needs the same environment credential; no model executes implicitly.

One step returns `ACTION_COMPLETED`, `MESSAGE`, `MODEL_STOPPED`, `MODEL_ERROR`,
`TOOL_ERROR`, `CHECKS_FAILED` or `BUDGET_EXHAUSTED`. The first two return CLI code 0;
other completed step outcomes return 1. Missing prerequisites/configuration
return 2. GNU Make may wrap child failures into its own nonzero code.

The current startup status remains `READY`/`AWAITING_INPUT`; it does not become a
solved-task status. `model_execution` changes from the obsolete `NOT_IMPLEMENTED`
to `NOT_RUN` by default, then `REQUESTED`, `COMPLETED` or `FAILED` during a step.
These are execution states, not task verdicts. Passing run_checks remains
`verification_status=NOT_ASSESSED`. No final verifier is added in Phase 3.

## Demonstration and limits

`make demo-model` uses the existing average-function fixture and real tools.
The mock supplies read_file → apply_patch → run_checks decisions. Actual baseline
failure and subsequent passing commands/diffs are written to a report. The mock
requires explicit construction and is not reachable through production provider
selection or missing-credential fallback.

The controller takes explicit message history for one step at a time; feedback
is bounded and marked as untrusted data. It does not preserve provider reasoning
state/native tool-message sessions or implement an autonomous repair loop. This
is a deliberate Phase 3 boundary, not a second controller architecture.

All Phase 2 sandbox/runtime limitations remain. In particular, real check/Git
integration tests require supported Linux namespaces, system Python and Git.
There is no unsandboxed fallback introduced by the model layer.

## Primary technical references

- DeepSeek public Chat Completions API: https://api-docs.deepseek.com/api/create-chat-completion/
- Qwen public compatible chat API: https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions
- Python HTTP client: https://docs.python.org/3/library/http.client.html

These references describe optional supported mechanisms, not the undisclosed
organizer configuration, a security audit, or proof of live provider compatibility.
