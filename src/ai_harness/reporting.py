"""Reports are projections of host execution records, not assistant summaries."""
from __future__ import annotations
from dataclasses import asdict
import json
import os
from pathlib import Path
from .verification import check_passed, failure_category


def build_report(state, result: dict, model: dict, registry: dict) -> dict:
    verification = result.get('verification') or state.verification or {}
    requested_status = result.get('task_result') or result.get('status') or 'BLOCKED'
    verified = (requested_status == 'VERIFIED' and verification.get('passed') is True
                and verification.get('stable_workspace') is True
                and verification.get('final_diff_inspected') is True)
    status = 'VERIFIED' if verified else ('INCOMPLETE' if requested_status in {'COMPLETED','VERIFIED'} else requested_status)
    if status not in {'VERIFIED','FAILED','INCOMPLETE','BLOCKED','BUDGET_EXHAUSTED'}: status='BLOCKED'
    history=[]
    for row in state.check_history:
        passed=check_passed(row,registry.get(row.get('name')))
        history.append({**row,'assessed_passed':passed,
                        'on_final_snapshot':bool(verified and row.get('source_snapshot') == verification.get('snapshot_sha256')),
                        'failure_category':None if passed else failure_category(row)})
    changes=result.get('changes')
    changed=[r['path'] for r in changes.get('status',[])] if isinstance(changes,dict) else list(state.modified_files)
    usage=state.usage
    return {'schema_version':1, 'status':status,'task':state.task.text if state.task else None,
            'model':model,'files_changed':sorted(set(changed)),
            'files_modified_by_harness':list(state.modified_files),
            'change_basis':'actual final Git status' if changes is not None else 'successful patch operations; net final diff not collected',
            'checks_run':history,
            'checks_passed':[r['execution_id'] for r in history if r['assessed_passed']],
            'checks_failed':[r['execution_id'] for r in history if not r['assessed_passed'] and r.get('command_started')],
            'checks_blocked':[r['execution_id'] for r in history if not r.get('command_started')],
            'checks_not_run':sorted(set(registry)-{r['name'] for r in history}),
            'targeted_checks_passed':[r['name'] for r in history if r['assessed_passed'] and r.get('scope')=='targeted'],
            'broader_checks_passed':[r['name'] for r in history if r['assessed_passed'] and r.get('scope')=='broad'],
            'iterations':usage.iterations,'model_calls':usage.model_calls,'tool_calls':usage.tool_calls,
            'test_runs':usage.test_executions,'recovery_attempts':usage.recovery_attempts,
            'token_usage':{'input_tokens':None if usage.unknown_input_usage_calls else usage.input_tokens,
                           'output_tokens':None if usage.unknown_output_usage_calls else usage.output_tokens,
                           'total_tokens':None if usage.unknown_model_usage_calls else usage.total_tokens,
                           'reported_input_subtotal':usage.input_tokens,'reported_output_subtotal':usage.output_tokens,
                           'reported_total_subtotal':usage.total_tokens,'unknown_usage_calls':usage.unknown_model_usage_calls,
                           'estimated_unreported_reservation':usage.estimated_tokens,
                           'estimate_note':'Estimates are not provider billing or exact tokenizer counts'},
            'runtime':state.elapsed_seconds,'resources':asdict(usage),'verification':verification,
            'final_state_verified':verified,'reason':result.get('reason') or result.get('error'),
            'limitations':[
                'Verification covers configured checks and eligible files, not hidden tests or official evaluation success.',
                'Mock/local HTTP runs prove mechanics/protocol only, not live model coding performance.',
                'Output may be redacted or truncated according to configured limits.',
                'The isolated runner requires supported Linux namespaces; it is not a kernel-exploit defense.',
                *([] if verified else ['The final repository state is not verified.'])]}


def terminal_report(report: dict) -> str:
    checks=report['checks_run']
    lines=[f"AI Coding Harness | {report['status']}",f"Task: {report['task']}",
           'Changes: '+(', '.join(report['files_changed']) or '(none recorded)'),
           'Change evidence: '+report['change_basis'],
           f"Model: {report['model'].get('provider')} / {report['model'].get('model_id')}"]
    for row in checks:
        verdict='PASSED' if row['assessed_passed'] else ('FAILED' if row.get('command_started') else 'BLOCKED')
        lines.append(f"Check {row['execution_id']} {row['name']}: {verdict}; exit={row.get('exit_code')}; final-snapshot={row['on_final_snapshot']}")
    lines += ['Checks not run: '+(', '.join(report['checks_not_run']) or '(none)'),
              'Final state verified: '+str(report['final_state_verified']),
              f"Calls: model={report['model_calls']} tools={report['tool_calls']} tests={report['test_runs']}; runtime={report['runtime']:.3f}s",
              'Token usage: '+json.dumps(report['token_usage']),
              'Uncertainties: '+' '.join(report['limitations'])]
    # Neutralize terminal control sequences from untrusted paths/tasks.
    return '\n'.join(''.join(c if c.isprintable() else c.encode('unicode_escape').decode() for c in line) for line in lines)+'\n'


def write_report(directory: Path, report: dict, redactor) -> dict:
    report=redactor.clean(report)
    content={'report.json':json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
             'summary.md':terminal_report(report)}
    paths={}
    for name,text in content.items():
        path=directory/name
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w',encoding='utf-8') as stream:
            stream.write(text)
        paths[name]=str(path)
    return paths
