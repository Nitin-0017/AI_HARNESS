"""Expected, user-actionable failures. Messages must never include credentials."""


class HarnessError(Exception):
    """Base class for controlled application failures."""


class ConfigurationError(HarnessError):
    pass


class EnvironmentValidationError(HarnessError):
    pass


class InputError(HarnessError):
    pass


class WorkspaceError(HarnessError):
    pass


class BudgetExceeded(HarnessError):
    pass
