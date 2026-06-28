"""Go dependency analyzer."""

import re
from pathlib import Path
from typing import Optional

from .base import BaseAnalyzer, DependencyInfo

STDLIB_PREFIXES = {
    "fmt", "os", "io", "net", "http", "strings", "strconv", "bytes",
    "errors", "log", "math", "sort", "sync", "time", "context", "encoding",
    "crypto", "path", "runtime", "reflect", "bufio", "testing", "unicode",
    "regexp", "flag", "hash", "compress", "archive", "database", "image",
    "text", "html", "xml", "debug", "go/", "internal/",
}


def _is_stdlib(path: str) -> bool:
    return not ("." in path.split("/")[0])


class GoAnalyzer(BaseAnalyzer):
    @property
    def language(self) -> str:
        return "Go"

    @property
    def extensions(self) -> list[str]:
        return [".go"]

    def extract_imports(self, source: str) -> list[DependencyInfo]:
        deps: dict[str, DependencyInfo] = {}
        # single import
        for i, line in enumerate(source.splitlines(), 1):
            m = re.match(r'^\s*import\s+"([^"]+)"', line)
            if m:
                self._add_import(deps, m.group(1), line.strip(), i)
        # grouped import block
        block_m = re.search(r'import\s*\(([\s\S]*?)\)', source)
        if block_m:
            for i, raw in enumerate(block_m.group(1).splitlines()):
                raw = raw.strip()
                m = re.match(r'^(?:\w+\s+)?"([^"]+)"', raw)
                if m:
                    self._add_import(deps, m.group(1), raw, i)
        return list(deps.values())

    def _add_import(self, deps, path, stmt, lineno):
        if _is_stdlib(path):
            return
        # package name is last element of module path
        pkg_name = path.split("/")[-1]
        if path not in deps:
            deps[path] = DependencyInfo(
                name=path,
                imported_name=pkg_name,
                import_line=lineno,
                import_statement=stmt,
            )

    def find_manifest(self, file_path: Path) -> Optional[Path]:
        for directory in [file_path.parent, *file_path.parents]:
            candidate = directory / "go.mod"
            if candidate.exists():
                return candidate
            if (directory / ".git").exists():
                break
        return None

    def parse_manifest(self, manifest_path: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        text = manifest_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            m = re.match(r"^\s*require\s+([\w\./\-]+)\s+(v[\w\.\-]+)", line)
            if m:
                result[m.group(1)] = m.group(2).lstrip("v")
            # multi-line require block
            m2 = re.match(r"^\s*([\w\./\-]+)\s+(v[\w\.\-]+)", line)
            if m2 and "/" in m2.group(1):
                result[m2.group(1)] = m2.group(2).lstrip("v")
        return result

    def apply_manifest_fixes(self, manifest_path: Path, updates: dict[str, str]) -> None:
        text = manifest_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        new_lines = []
        for line in lines:
            replaced = False
            for pkg, ver in updates.items():
                if pkg in line and re.search(r"v[\w\.\-]+", line):
                    line = re.sub(r"v[\w\.\-]+", f"v{ver}", line)
                    replaced = True
                    break
            new_lines.append(line)
        manifest_path.write_text("".join(new_lines), encoding="utf-8")
