"""Evidence classification and assessment; never executes or trusts model claims."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any


def failure_category(check: dict) -> str:
    text = str(check.get('error') or '') + '\n' + check.get('stdout', '') + '\n' + check.get('stderr', '')
    if check.get('timed_out'):
        return 'timeout'
    if check.get('command_started') is False or 'Isolation could not be established' in text:
        return 'environment_failure'
    if re.search(r'ModuleNotFoundError|ImportError|No module named|command not found|not installed', text):
        return 'missing_dependency'
    if re.search(r'SyntaxError|IndentationError|TabError', text):
        return 'syntax_error'
    if re.search(r'AssertionError|FAIL:|FAILED.*test|\bassert\b', text):
        return 'test_assertion_failure'
    if re.search(r'PatchError|patch.*(match|digest|invalid)|INSPECTION_REQUIRED', text, re.I):
        return 'invalid_patch'
    if check.get('output_limit_exceeded'):
        return 'output_limit'
    if check.get('exit_code') not in {None, 0}:
        return 'command_failure'
    return 'unknown_failure'


def fingerprint(category: str, command: list | tuple, error: str, relevant_file: str = '') -> str:
    normalized = re.sub(r'0x[0-9a-fA-F]+', '<address>', error)
    normalized = re.sub(r'/tmp/[^/\s\"\']+', '/tmp/<run>', normalized)
    normalized = re.sub(r'Ran (\d+) tests? in [0-9.]+s', r'Ran \1 tests in <duration>', normalized)
    normalized = re.sub(r'\b[0-9a-f]{32,64}\b', '<digest>', normalized)
    payload = [category, list(command), normalized.strip(), relevant_file]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def check_passed(result: dict, spec) -> bool:
    passed = (result.get('command_started') is True and type(result.get('exit_code')) is int
              and result['exit_code'] == 0 and result.get('timed_out') is False
              and result.get('output_limit_exceeded') is False and result.get('error') is None)
    if not passed or spec is None:
        return False
    output = result.get('stdout', '') + '\n' + result.get('stderr', '')
    argv = list(spec.argv)
    if any(argv[i:i+2] == ['-m','unittest'] for i in range(len(argv)-1)):
        return bool(re.search(r'Ran [1-9][0-9]* tests?\b', output)) and not re.search(r'FAILED\s*\(', output)
    if any(argv[i:i+2] == ['-m','pytest'] for i in range(len(argv)-1)) or str(argv[0]).endswith('/pytest'):
        return bool(re.search(r'\b[1-9][0-9]* passed\b', output)) and not re.search(r'\b[1-9][0-9]* (failed|errors?)\b', output)
    return True  # Explicit trusted command's exit-status contract, not test-count proof.


@dataclass(frozen=True)
class VerificationResult:
    status: str
    checks: list[dict[str, Any]]
    passed: bool
    failed: list[str]
    blocked: list[str]
    evidence: dict[str, Any]
    duration: float

    def to_dict(self) -> dict:
        value = asdict(self)
        value.update(self.evidence)
        value['results'] = value['checks']  # Existing controller/report API.
        value['source'] = 'RepositoryTools.run_checks'
        value['scope'] = 'Configured checks and eligible workspace files; not hidden-test or task-correctness proof'
        return value


def assess(results: list[dict], registry: dict, before: str | None, after: str) -> VerificationResult:
    names = [r.get('name') for r in results]
    required = {name for name,spec in registry.items() if spec.required}
    complete = bool(required) and bool(results) and len(names) == len(set(names)) and required <= set(names) <= set(registry)
    stable = before is not None and before == after
    failed=[]; blocked=[]; failures=[]
    for result in results:
        spec = registry.get(result.get('name'))
        if check_passed(result, spec):
            continue
        category = failure_category(result)
        name = str(result.get('name'))
        (blocked if category in {'environment_failure','missing_dependency'} else failed).append(name)
        command = list(spec.argv) if spec else result.get('argv', [])
        output = result.get('stdout','') + '\n' + result.get('stderr','') + '\n' + str(result.get('error') or '')
        matches = re.findall(r'(?:File ")([^"\n]+)', output)
        relevant = matches[-1] if matches else ''
        failures.append({'category': category, 'check': name, 'command': command,
                         'relevant_file': relevant, 'fingerprint': fingerprint(category, command, output, relevant),
                         'stdout': result.get('stdout',''), 'stderr': result.get('stderr',''),
                         'error': result.get('error')})
    passed = complete and stable and not failed and not blocked
    status = 'VERIFIED' if passed else ('BLOCKED' if blocked else ('FAILED' if failed else 'INCOMPLETE'))
    evidence = {'complete_registry': complete, 'stable_workspace': stable, 'snapshot_sha256': after,
                'before_snapshot_sha256': before, 'failures': failures,
                'checks_not_run': sorted(set(registry) - set(names)), 'final_diff_inspected': False}
    duration = sum(float(r.get('duration_seconds', 0)) for r in results)
    return VerificationResult(status, results, bool(passed), failed, blocked, evidence, duration)


def recovery_context(state, assessment: dict | None, modified: list[str], inspected: dict,
                     previous_repair: dict | None, error: dict | None = None) -> dict:
    from .context import clip
    failures = (assessment or {}).get('failures', [])
    compact=[]
    for item in failures[:3]:
        compact.append({**{k:v for k,v in item.items() if k not in {'stdout','stderr'}},
                        'stdout':clip(item.get('stdout',''),1600), 'stderr':clip(item.get('stderr',''),2400)})
    return {'task':clip(state.task.text if state.task else '',1200),
            'files_changed':modified[:16], 'failing_checks':compact, 'tool_or_model_error':error,
            'changed_code':{p:clip(inspected[p][1],1600) for p in modified[:3] if p in inspected},
            'previous_repair':previous_repair,
            'why_failed':'Use the recorded failing command/output or tool error; no cause inferred from model prose.',
            'remaining_budget':{'model_calls':max(0,state.budgets.max_model_calls-state.usage.model_calls),
                                'tool_calls':max(0,state.budgets.max_tool_calls-state.usage.tool_calls),
                                'iterations':max(0,state.budgets.max_iterations-state.usage.iterations),
                                'seconds':max(0,round(state.budgets.max_seconds-state.elapsed_seconds,3)),
                                'tokens_after_reported_and_estimated_usage':max(0,state.budgets.max_total_tokens-state.usage.total_tokens-state.usage.estimated_tokens)},
            'instruction':'Use the new failure evidence to choose a different action; do not repeat an unchanged failed action.'}
