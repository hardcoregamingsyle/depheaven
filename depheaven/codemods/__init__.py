"""AST-based code transformations for known breaking changes — no LLM needed."""

from .python_transforms import apply_python_codemods
from .js_transforms import apply_js_codemods

__all__ = ["apply_python_codemods", "apply_js_codemods", "apply_codemods"]


def apply_codemods(source: str, language: str, package: str, from_ver: str, to_ver: str) -> tuple[str, list[str]]:
    """
    Apply known AST/regex codemods for a package upgrade.
    Returns (new_source, list_of_changes_made).
    """
    if "python" in language.lower():
        return apply_python_codemods(source, package, from_ver, to_ver)
    elif "javascript" in language.lower() or "typescript" in language.lower():
        return apply_js_codemods(source, package, from_ver, to_ver)
    return source, []
