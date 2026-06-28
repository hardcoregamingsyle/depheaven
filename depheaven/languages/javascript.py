"""JavaScript / TypeScript dependency analyzer."""

import json
import re
from pathlib import Path
from typing import Optional

from .base import BaseAnalyzer, DependencyInfo

# node built-ins to ignore
NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "constants", "crypto", "dgram", "dns", "domain", "events", "fs", "http",
    "http2", "https", "inspector", "module", "net", "os", "path", "perf_hooks",
    "process", "punycode", "querystring", "readline", "repl", "stream",
    "string_decoder", "timers", "tls", "trace_events", "tty", "url", "util",
    "v8", "vm", "worker_threads", "zlib",
}


def _pkg_from_specifier(spec: str) -> Optional[str]:
    """Extract npm package name from an import specifier."""
    spec = spec.strip("'\"`")
    if spec.startswith(".") or spec.startswith("/"):
        return None
    # scoped packages: @scope/pkg
    if spec.startswith("@"):
        parts = spec.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return None
    # regular: pkg or pkg/subpath
    pkg = spec.split("/")[0]
    if pkg in NODE_BUILTINS or pkg.startswith("node:"):
        return None
    return pkg


class JavaScriptAnalyzer(BaseAnalyzer):
    @property
    def language(self) -> str:
        return "JavaScript/TypeScript"

    @property
    def extensions(self) -> list[str]:
        return [".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"]

    def extract_imports(self, source: str) -> list[DependencyInfo]:
        deps: dict[str, DependencyInfo] = {}
        patterns = [
            # import ... from 'pkg'
            r"""(?:import|export)\s+(?:[\s\S]*?\s+from\s+)?['"`]([^'"`]+)['"`]""",
            # require('pkg')
            r"""require\s*\(\s*['"`]([^'"`]+)['"`]\s*\)""",
            # import('pkg')
            r"""import\s*\(\s*['"`]([^'"`]+)['"`]\s*\)""",
        ]
        for i, line in enumerate(source.splitlines(), 1):
            for pattern in patterns:
                for m in re.finditer(pattern, line):
                    spec = m.group(1)
                    pkg = _pkg_from_specifier(spec)
                    if pkg and pkg not in deps:
                        deps[pkg] = DependencyInfo(
                            name=pkg,
                            imported_name=pkg,
                            import_line=i,
                            import_statement=line.strip(),
                        )
        return list(deps.values())

    def find_manifest(self, file_path: Path) -> Optional[Path]:
        for directory in [file_path.parent, *file_path.parents]:
            candidate = directory / "package.json"
            if candidate.exists():
                return candidate
            if (directory / ".git").exists():
                break
        return None

    def parse_manifest(self, manifest_path: Path) -> dict[str, str]:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        result: dict[str, str] = {}
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            for pkg, ver in data.get(section, {}).items():
                result[pkg] = ver.lstrip("^~>=<!")
        return result

    def apply_manifest_fixes(self, manifest_path: Path, updates: dict[str, str]) -> None:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            if section in data:
                for pkg in list(data[section]):
                    if pkg in updates:
                        prefix = ""
                        old = data[section][pkg]
                        if old and old[0] in "^~":
                            prefix = old[0]
                        data[section][pkg] = f"{prefix}{updates[pkg]}"
        # add missing to dependencies
        deps = data.setdefault("dependencies", {})
        all_existing = set()
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            all_existing.update(data.get(section, {}).keys())
        for pkg, ver in updates.items():
            if pkg not in all_existing:
                deps[pkg] = f"^{ver}"
        manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
