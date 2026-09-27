"""Connect one explicitly requested tool to the existing startup/state layer."""
from __future__ import annotations

import json
import os
from typing import Any

from .errors import InputError
from .startup import StartupResult
from .telemetry import EventLog, Redactor, write_state
from .tools import RepositoryTools


def execute_tool(startup: StartupResult, name: str, arguments: dict[str, Any], redactor: Redactor) -> int:
    if startup.workspace is None:
        raise InputError('A separate --workspace is required for repository tools')
    state = startup.state
    state.current_action = name
    outcome = None
    with EventLog(startup.run_dir, state.run_id, redactor, startup.config.log_level,
                  filename='tool-events.jsonl') as log:
        try:
            with RepositoryTools(startup.workspace, limits=startup.config.tools,
                                 checks=startup.config.checks, state=state, redactor=redactor,
                                 emit=log.emit) as tools:
                outcome = tools.call(name, arguments)
                if name == 'run_checks':
                    state.verification_status = 'NOT_ASSESSED'
        finally:
            state.current_action = None
            artifacts = startup.snapshot['artifacts']
            startup.snapshot.clear()
            startup.snapshot.update(redactor.clean(state.to_dict()))
            startup.snapshot['artifacts'] = artifacts
            artifacts['tool_events_file'] = str(startup.run_dir / 'tool-events.jsonl')
            if outcome is not None:
                path = startup.run_dir / 'tool_result.json'
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600),
                               'w', encoding='utf-8') as stream:
                    json.dump(redactor.clean(outcome), stream, indent=2, ensure_ascii=False)
                    stream.write('\n')
                startup.snapshot['tool_result'] = outcome
                artifacts['tool_result_file'] = str(path)
            write_state(startup.run_dir, startup.snapshot, redactor)
    return 1 if name == 'run_checks' and not outcome['all_passed'] else 0
