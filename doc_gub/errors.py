"""Domain exceptions shown to CLI users without tracebacks."""


class DocGubError(Exception):
    """Expected, actionable application error."""


class AIProviderError(DocGubError):
    """The configured model provider could not be reached or answered incorrectly."""


class AITimeoutError(AIProviderError):
    """The configured model provider timed out."""


class InvalidAIResponseError(DocGubError):
    """The model response did not conform to the documented JSON contract."""
