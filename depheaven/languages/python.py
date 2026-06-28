"""Python dependency analyzer."""

import re
from pathlib import Path
from typing import Optional

from .base import BaseAnalyzer, DependencyInfo

# stdlib modules to ignore
STDLIB = {
    "abc", "ast", "asyncio", "base64", "builtins", "collections", "contextlib",
    "copy", "dataclasses", "datetime", "enum", "functools", "hashlib", "http",
    "io", "itertools", "json", "logging", "math", "os", "pathlib", "pickle",
    "platform", "pprint", "queue", "random", "re", "shutil", "signal",
    "socket", "sqlite3", "string", "struct", "subprocess", "sys", "tempfile",
    "threading", "time", "traceback", "types", "typing", "unittest", "urllib",
    "uuid", "warnings", "weakref", "xml", "zipfile", "zlib",
    # common aliases
    "typing_extensions",
}

# map import name → PyPI package name when they differ
IMPORT_TO_PACKAGE = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "attr": "attrs",
    "psycopg2": "psycopg2-binary",
    "usaddress": "usaddress",
    "gi": "PyGObject",
    "wx": "wxPython",
}


class PythonAnalyzer(BaseAnalyzer):
    @property
    def language(self) -> str:
        return "Python"

    @property
    def extensions(self) -> list[str]:
        return [".py"]

    def extract_imports(self, source: str) -> list[DependencyInfo]:
        deps: dict[str, DependencyInfo] = {}
        for i, line in enumerate(source.splitlines(), 1):
            line = line.strip()
            # import X, import X as Y
            m = re.match(r"^import\s+([\w.]+)", line)
            if m:
                pkg = m.group(1).split(".")[0]
                self._add(deps, pkg, line, i)
                continue
            # from X import ...
            m = re.match(r"^from\s+([\w.]+)\s+import", line)
            if m:
                pkg = m.group(1).split(".")[0]
                if not pkg.startswith("."):
                    self._add(deps, pkg, line, i)
        return list(deps.values())

    def _add(self, deps, imported_name, stmt, lineno):
        if imported_name in STDLIB or imported_name.startswith("_"):
            return
        if imported_name in deps:
            return
        package_name = IMPORT_TO_PACKAGE.get(imported_name, imported_name)
        deps[imported_name] = DependencyInfo(
            name=package_name,
            imported_name=imported_name,
            import_line=lineno,
            import_statement=stmt,
        )

    def find_manifest(self, file_path: Path) -> Optional[Path]:
        for directory in [file_path.parent, *file_path.parents]:
            for name in ("requirements.txt", "pyproject.toml", "Pipfile", "setup.cfg"):
                candidate = directory / name
                if candidate.exists():
                    return candidate
            if (directory / ".git").exists():
                break
        return None

    def parse_manifest(self, manifest_path: Path) -> dict[str, str]:
        text = manifest_path.read_text(encoding="utf-8")
        result: dict[str, str] = {}
        name = manifest_path.name

        if name == "requirements.txt":
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # strip extras like package[extra]
                m = re.match(r"^([\w\-\.]+)(\[[\w,]+\])?\s*([><=!~]+)\s*([\w\.]+)", line)
                if m:
                    result[m.group(1).lower().replace("-", "_")] = m.group(4)
                else:
                    m2 = re.match(r"^([\w\-\.]+)", line)
                    if m2:
                        result[m2.group(1).lower().replace("-", "_")] = ""

        elif name == "pyproject.toml":
            try:
                import tomllib  # type: ignore
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore
                except ImportError:
                    return result
            try:
                data = tomllib.loads(text)
            except Exception:
                return result
            deps = (
                data.get("project", {}).get("dependencies", [])
                or data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            )
            if isinstance(deps, list):
                for dep in deps:
                    m = re.match(r"^([\w\-\.]+).*?([0-9]+\.[0-9]+[^\s,;]*)?", dep)
                    if m:
                        result[m.group(1).lower().replace("-", "_")] = m.group(2) or ""
            elif isinstance(deps, dict):
                for pkg, ver in deps.items():
                    if isinstance(ver, str):
                        result[pkg.lower().replace("-", "_")] = ver.lstrip("^~>=<!")

        elif name == "Pipfile":
            in_packages = False
            for line in text.splitlines():
                if line.strip().startswith("[packages]"):
                    in_packages = True
                    continue
                if line.strip().startswith("[") and in_packages:
                    in_packages = False
                if in_packages:
                    m = re.match(r'^([\w\-]+)\s*=\s*["\']([^"\']*)["\']', line)
                    if m:
                        result[m.group(1).lower().replace("-", "_")] = m.group(2).lstrip("^~>=<!")

        return result

    def apply_manifest_fixes(self, manifest_path: Path, updates: dict[str, str]) -> None:
        name = manifest_path.name
        text = manifest_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)

        if name == "requirements.txt":
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    new_lines.append(line)
                    continue
                m = re.match(r"^([\w\-\.]+)", stripped)
                if m:
                    pkg_key = m.group(1).lower().replace("-", "_")
                    if pkg_key in updates:
                        # preserve any extras
                        extras_m = re.match(r"^([\w\-\.]+)(\[[^\]]+\])?", stripped)
                        extras = extras_m.group(2) or "" if extras_m else ""
                        new_lines.append(f"{m.group(1)}{extras}>={updates[pkg_key]}\n")
                        continue
                new_lines.append(line)
            # add missing packages
            existing = set(re.match(r"^([\w\-\.]+)", l.strip()).group(1).lower().replace("-", "_")
                           for l in lines if l.strip() and not l.strip().startswith("#")
                           and re.match(r"^([\w\-\.]+)", l.strip()))
            for pkg, ver in updates.items():
                if pkg not in existing:
                    new_lines.append(f"{pkg}>={ver}\n")
            manifest_path.write_text("".join(new_lines), encoding="utf-8")

    def normalize_package_name(self, name: str) -> str:
        return name.lower().replace("-", "_").replace(".", "_")
