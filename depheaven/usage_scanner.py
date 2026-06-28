"""
Scan source code to find which APIs from a package are actually being used.

This lets DepHeaven say "you call .dict() on line 42 — that was renamed to
.model_dump() in pydantic v2" rather than generic warnings.

Works for Python (AST-based) and JS/TS (regex-based, no parser needed).
No LLM. Pure static analysis.
"""

import ast
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ApiUsage:
    """A specific API call found in source code."""
    symbol: str             # e.g. ".dict()" or "np.bool"
    line: int
    column: int
    context: str            # the source line (for display)
    package_hint: str       # which package this likely belongs to


# ── Python AST scanner ────────────────────────────────────────────────────────

class _PythonUsageVisitor(ast.NodeVisitor):
    def __init__(self, source_lines: list[str], package_aliases: dict[str, str]):
        self.usages: list[ApiUsage] = []
        self.source_lines = source_lines
        # maps local alias → package name, e.g. {"np": "numpy", "pd": "pandas"}
        self.aliases = package_aliases

    def _ctx_line(self, lineno: int) -> str:
        try:
            return self.source_lines[lineno - 1].strip()
        except IndexError:
            return ""

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Catches: obj.method(), obj.attr
        owner = _extract_name(node.value)
        if owner and owner in self.aliases:
            pkg = self.aliases[owner]
            symbol = f"{owner}.{node.attr}"
            self.usages.append(ApiUsage(
                symbol=symbol,
                line=node.lineno,
                column=node.col_offset,
                context=self._ctx_line(node.lineno),
                package_hint=pkg,
            ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Catches method chains: model.dict(), model.json()
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            owner = _extract_name(node.func.value)
            # attribute calls on any object — record the method name
            if attr in _WATCHED_METHODS:
                pkg = self.aliases.get(owner or "", "unknown")
                symbol = f".{attr}()"
                self.usages.append(ApiUsage(
                    symbol=symbol,
                    line=node.lineno,
                    column=node.col_offset,
                    context=self._ctx_line(node.lineno),
                    package_hint=pkg,
                ))
        self.generic_visit(node)


# Methods we always track regardless of which object they're called on,
# because they tend to be package-specific patterns
_WATCHED_METHODS = {
    "dict", "json", "parse_obj", "parse_raw", "schema", "copy",  # pydantic v1
    "execute", "query", "add", "commit",                           # sqlalchemy
    "render", "hydrate",                                            # react
    "get", "post", "put", "delete", "patch",                       # requests/axios
}


def _extract_name(node: ast.expr) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _extract_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _collect_aliases(source: str) -> dict[str, str]:
    """
    Parse import statements to build alias→package map.
    e.g. `import numpy as np` → {"np": "numpy", "numpy": "numpy"}
         `from pydantic import BaseModel` → {"BaseModel": "pydantic"}
    """
    aliases: dict[str, str] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return aliases

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                pkg = alias.name.split(".")[0]
                aliases[local] = pkg
                aliases[pkg] = pkg
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                pkg = node.module.split(".")[0]
                for alias in node.names:
                    local = alias.asname or alias.name
                    aliases[local] = pkg
    return aliases


def scan_python(source: str, package: str) -> list[ApiUsage]:
    """Find all API usages of `package` in Python source."""
    aliases = _collect_aliases(source)
    # only keep aliases that belong to this package
    pkg_aliases = {k: v for k, v in aliases.items() if v.lower() == package.lower()
                   or v.lower().replace("-", "_") == package.lower().replace("-", "_")}
    if not pkg_aliases:
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    visitor = _PythonUsageVisitor(lines, pkg_aliases)
    visitor.visit(tree)
    return visitor.usages


# ── JS/TS regex scanner ───────────────────────────────────────────────────────

# Known symbols per package that we watch for
_JS_WATCHED: dict[str, list[str]] = {
    "react": [
        "ReactDOM.render", "ReactDOM.hydrate", "componentWillMount",
        "componentWillReceiveProps", "componentWillUpdate",
        "createFactory", "React.createClass",
    ],
    "axios": ["CancelToken", "axios.Cancel", "axios.Axios"],
    "webpack": [
        "require.extensions", "futureEmitAssets", "hashedModuleIds", "namedModules",
    ],
    "express": ["app.del(", "req.param("],
    "vue": [
        "new Vue(", "Vue.set(", "Vue.delete(", r"\$on\(", r"\$off\(", r"\$once\(",
        r"\.filters\s*:", "beforeDestroy", "destroyed",
    ],
    "lodash": ["_.pluck(", "_.any(", "_.all(", "_.contains(", "_.first(", "_.rest("],
    "jest": ["jest.genMockFromModule", "testEnvironment.*jsdom"],
}


def scan_js(source: str, package: str) -> list[ApiUsage]:
    """Find API usages of `package` in JS/TS source using regex."""
    watched = _JS_WATCHED.get(package.lower(), [])
    if not watched:
        return []

    usages: list[ApiUsage] = []
    lines = source.splitlines()

    for pattern in watched:
        rx = re.compile(pattern)
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                usages.append(ApiUsage(
                    symbol=pattern.replace(r"\(", "(").replace(r"\$", "$"),
                    line=i,
                    column=0,
                    context=line.strip(),
                    package_hint=package,
                ))

    return usages


# ── Unified entry point ────────────────────────────────────────────────────────

def scan_usages(source: str, language: str, package: str) -> list[ApiUsage]:
    """Scan source for API usages of `package`. Language-agnostic entry point."""
    if "python" in language.lower():
        return scan_python(source, package)
    elif "javascript" in language.lower() or "typescript" in language.lower():
        return scan_js(source, package)
    return []


# ── Cross-reference usages against changelog ──────────────────────────────────

def match_usages_to_changes(
    usages: list[ApiUsage],
    changes,  # list[ParsedChange] from changelog_parser
) -> list[tuple[ApiUsage, object]]:
    """
    Return pairs of (usage, change) where the usage appears to be affected
    by a known breaking change.
    """
    hits: list[tuple[ApiUsage, object]] = []
    for usage in usages:
        for change in changes:
            old = (change.old_api or change.affected_symbol or "").lower().strip("`")
            sym = usage.symbol.lower().strip("`.()")
            if old and (old in sym or sym in old):
                hits.append((usage, change))
                break
    return hits
