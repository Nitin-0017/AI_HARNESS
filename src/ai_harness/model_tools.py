"""Model-facing descriptions of the unchanged RepositoryTools.call contract."""
from .model_types import ToolDefinition


def repository_tool_definitions() -> tuple[ToolDefinition, ...]:
    text = {'type': 'string'}
    path = {'type': 'string', 'minLength': 1, 'maxLength': 4096}
    integer = {'type': 'integer', 'minimum': 1}
    def definition(name, description, properties, required=()):
        return ToolDefinition(name, description, {'type': 'object', 'properties': properties,
                                                'required': list(required), 'additionalProperties': False})
    edit = {'type': 'object', 'properties': {
        'path': path, 'old': text, 'new': text,
        'operation': {'type': 'string', 'enum': ['replace', 'create', 'delete']},
        'expected_sha256': {'type': ['string', 'null']}},
        'required': ['path'], 'additionalProperties': False}
    return (
        definition('list_files', 'List files in the target workspace.', {'path': path, 'pattern': text}),
        definition('search_code', 'Search literal code text.', {'query': {'type': 'string', 'minLength': 1},
                   'path': path, 'pattern': text, 'case_sensitive': {'type': 'boolean'}}, ('query',)),
        definition('read_file', 'Read UTF-8 text and its SHA-256.', {'path': path, 'start_line': integer,
                   'end_line': {'type': ['integer', 'null'], 'minimum': 1}}, ('path',)),
        definition('apply_patch', 'Apply exact unique replacements, creates or digest-guarded deletes. Never fuzzy match.',
                   {'edits': {'type': 'array', 'items': edit, 'minItems': 1, 'maxItems': 20}, 'dry_run': {'type': 'boolean'}}, ('edits',)),
        definition('run_checks', 'Run trusted named checks; never supply shell commands.',
                   {'names': {'type': ['array', 'null'], 'items': {'type': 'string', 'minLength': 1}, 'minItems': 1, 'maxItems': 32}}),
        definition('get_changes', 'Inspect real repository Git status and diffs.', {}),
    )
