"""Language analyzers for DepHeaven."""

from .python import PythonAnalyzer
from .javascript import JavaScriptAnalyzer
from .go import GoAnalyzer

ANALYZERS = [
    PythonAnalyzer(),
    JavaScriptAnalyzer(),
    GoAnalyzer(),
]

EXTENSION_MAP: dict[str, "BaseAnalyzer"] = {}
for _analyzer in ANALYZERS:
    for _ext in _analyzer.extensions:
        EXTENSION_MAP[_ext] = _analyzer


def get_analyzer(file_path):
    """Return the appropriate analyzer for the given file path, or None."""
    from pathlib import Path
    ext = Path(file_path).suffix.lower()
    return EXTENSION_MAP.get(ext)
