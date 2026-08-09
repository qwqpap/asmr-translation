class AsmrLrcError(Exception):
    """Base class for expected, user-facing failures."""


class EnvironmentError(AsmrLrcError):
    """A required local service or executable is unavailable."""


class CacheError(AsmrLrcError):
    """Cached data cannot be read or validated."""


class AsrError(AsmrLrcError):
    """Transcription failed."""


class TranslationError(AsmrLrcError):
    """Translation failed or returned invalid data."""


class LrcError(AsmrLrcError):
    """LRC validation or output failed."""
