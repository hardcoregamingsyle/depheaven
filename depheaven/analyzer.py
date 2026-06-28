"""Main analysis orchestrator for DepHeaven."""

from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .languages import get_analyzer
from .languages.base import AnalysisResult, DependencyInfo
from .registry import pypi_latest, npm_latest


# Known breaking change notes keyed by (package_name, from_major)
BREAKING_CHANGES: dict[tuple[str, str], str] = {
    ("django", "2"): "Django 2→3: url() removed, use path()/re_path()",
    ("django", "3"): "Django 3→4: ugettext() removed, use gettext(); DEFAULT_AUTO_FIELD required",
    ("flask", "1"): "Flask 1→2: before_first_request removed; Blueprints need name param",
    ("sqlalchemy", "1"): "SQLAlchemy 1→2: Session.execute() API changed; legacy Query style removed",
    ("pydantic", "1"): "Pydantic v1→v2: .dict()→.model_dump(), .json()→.model_json(), validators rewritten",
    ("numpy", "1"): "NumPy 1→2: np.bool/np.int/np.float aliases removed; use built-in types",
    ("celery", "4"): "Celery 4→5: task_always_eager removed; result backend API changed",
    ("pytest", "6"): "pytest 6→7: warns() match required; tmp_path_retention_count added",
    ("react", "17"): "React 17→18: createRoot() replaces ReactDOM.render(); concurrent mode default",
    ("react", "16"): "React 16→17: no new features; prepares for concurrent mode",
    ("webpack", "4"): "Webpack 4→5: require.extensions removed; output.futureEmitAssets removed",
    ("express", "4"): "Express 4→5: path params allow optional {}; res.redirect() always absolute",
    ("axios", "0"): "Axios 0.x→1.x: default JSON serialization changed; CanceledError renamed",
}


def _get_latest_version(dep: DependencyInfo, language: str) -> Optional[str]:
    """Fetch latest version from the appropriate registry."""
    try:
        if "python" in language.lower():
            return pypi_latest(dep.name)
        elif "javascript" in language.lower() or "typescript" in language.lower():
            return npm_latest(dep.name)
        # Go modules don't have a simple registry API — skip version check
    except Exception:
        pass
    return None


def _check_breaking(dep: DependencyInfo, language: str) -> tuple[bool, Optional[str]]:
    """Check known breaking changes between current and latest major version."""
    if not dep.current_version or not dep.latest_version:
        return False, None
    try:
        cur_ver = dep.current_version.lstrip("^~>=<! ")
        cur_major = cur_ver.split(".")[0]
        lat_major = dep.latest_version.split(".")[0]
        if cur_major == lat_major:
            return False, None
        pkg_key = dep.name.lower().replace("-", "_")
        notes = BREAKING_CHANGES.get((pkg_key, cur_major))
        if notes:
            return True, notes
        # No known notes but major version bump = potential breaking
        if int(lat_major) > int(cur_major):
            return True, f"Major version bump {cur_major}→{lat_major}: review changelog for breaking changes"
    except (ValueError, IndexError):
        pass
    return False, None


def analyze_file(file_path: Path, offline: bool = False) -> AnalysisResult:
    """Analyze a single file for dependency issues."""
    analyzer = get_analyzer(file_path)
    if analyzer is None:
        return AnalysisResult(
            file_path=file_path,
            language="Unknown",
            errors=[f"No analyzer available for {file_path.suffix} files"],
        )

    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return AnalysisResult(
            file_path=file_path,
            language=analyzer.language,
            errors=[str(e)],
        )

    deps = analyzer.extract_imports(source)
    manifest_path = analyzer.find_manifest(file_path)
    manifest: dict[str, str] = {}

    if manifest_path:
        try:
            manifest = analyzer.parse_manifest(manifest_path)
        except Exception as e:
            pass  # continue without manifest data

    # Annotate deps with manifest info
    for dep in deps:
        norm = analyzer.normalize_package_name(dep.name)
        # Try exact and common variants
        for key in (norm, dep.name.lower(), dep.name):
            if key in manifest:
                dep.in_manifest = True
                dep.current_version = manifest[key]
                break

    # Fetch latest versions (in parallel, unless offline)
    if not offline:
        def fetch(dep: DependencyInfo) -> DependencyInfo:
            dep.latest_version = _get_latest_version(dep, analyzer.language)
            return dep

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(fetch, dep): dep for dep in deps}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass

    # Check for breaking changes
    for dep in deps:
        has_breaking, notes = _check_breaking(dep, analyzer.language)
        dep.has_breaking_changes = has_breaking
        dep.breaking_change_notes = notes

    return AnalysisResult(
        file_path=file_path,
        language=analyzer.language,
        dependencies=deps,
        manifest_path=manifest_path,
    )


def analyze_directory(
    directory: Path,
    offline: bool = False,
    recursive: bool = True,
) -> list[AnalysisResult]:
    """Analyze all supported source files in a directory."""
    from .languages import EXTENSION_MAP

    results: list[AnalysisResult] = []
    pattern = "**/*" if recursive else "*"

    for file_path in sorted(directory.glob(pattern)):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in EXTENSION_MAP:
            continue
        # Skip node_modules, .venv, __pycache__, etc.
        parts = file_path.parts
        skip_dirs = {
            "node_modules", ".venv", "venv", "__pycache__", ".git",
            ".tox", "dist", "build", "target", ".mypy_cache",
        }
        if any(part in skip_dirs for part in parts):
            continue

        result = analyze_file(file_path, offline=offline)
        results.append(result)

    return results
