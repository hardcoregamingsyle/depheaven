"""Main analysis orchestrator for DepHeaven."""

from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .languages import get_analyzer
from .languages.base import AnalysisResult, DependencyInfo, DependencyInsight
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
    ("vue", "2"): "Vue 2→3: createApp() replaces new Vue(); $on/$off/$once removed",
    ("lodash", "3"): "Lodash 3→4: _.pluck removed, use _.map; _.any→_.some; _.all→_.every",
}


def _get_latest_version(dep: DependencyInfo, language: str) -> Optional[str]:
    try:
        if "python" in language.lower():
            return pypi_latest(dep.name)
        elif "javascript" in language.lower() or "typescript" in language.lower():
            return npm_latest(dep.name)
    except Exception:
        pass
    return None


def _check_breaking(dep: DependencyInfo, language: str) -> tuple[bool, Optional[str]]:
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
        if int(lat_major) > int(cur_major):
            return True, f"Major version bump {cur_major}→{lat_major}: review changelog for breaking changes"
    except (ValueError, IndexError):
        pass
    return False, None


def _enrich_with_insights(
    result: AnalysisResult,
    source: str,
    language: str,
    deep: bool = False,
) -> None:
    """Fetch changelogs and apply codemods for deps that have upgrades."""
    from .changelog import pypi_changelog, npm_changelog
    from .codemods import apply_codemods

    use_ollama = False
    ollama_model = None
    if deep:
        try:
            from . import ollama_client
            if ollama_client.is_available():
                use_ollama = True
                ollama_model = ollama_client.best_model()
        except Exception:
            pass

    current_source = source
    insights: list[DependencyInsight] = []

    for dep in result.dependencies:
        if dep.status not in ("outdated", "breaking"):
            continue
        if not dep.current_version or not dep.latest_version:
            continue
        if dep.current_version == dep.latest_version:
            continue

        insight = DependencyInsight(
            package=dep.name,
            from_version=dep.current_version,
            to_version=dep.latest_version,
        )

        # 1. Fetch changelog text from public APIs (no auth)
        changelog_text: Optional[str] = None
        try:
            if "python" in language.lower():
                changelog_text = pypi_changelog(dep.name, dep.current_version, dep.latest_version)
            elif "javascript" in language.lower() or "typescript" in language.lower():
                changelog_text = npm_changelog(dep.name, dep.current_version, dep.latest_version)
        except Exception:
            pass

        # 2. Ask Ollama to summarize (only when --deep and Ollama is running)
        if changelog_text and use_ollama:
            try:
                from . import ollama_client
                analysis = ollama_client.analyze_changelog(
                    dep.name,
                    dep.current_version,
                    dep.latest_version,
                    changelog_text,
                    model=ollama_model,
                )
                insight.changelog_summary = analysis.get("summary")
                insight.api_changes = analysis.get("api_changes", [])
                insight.migration_steps = analysis.get("migration_steps", [])
                insight.ollama_used = True
                if analysis.get("breaking"):
                    dep.has_breaking_changes = True
                if insight.api_changes and not dep.breaking_change_notes:
                    dep.breaking_change_notes = "; ".join(insight.api_changes[:3])
            except Exception:
                pass

        # 3. Apply AST/regex codemods — always, no LLM needed
        try:
            new_source, codemod_changes = apply_codemods(
                current_source, language, dep.name,
                dep.current_version, dep.latest_version,
            )
            if codemod_changes:
                insight.codemod_changes = codemod_changes
                current_source = new_source
        except Exception:
            pass

        # 4. Ask Ollama for a code suggestion when codemods didn't cover it
        if use_ollama and insight.api_changes and not insight.codemod_changes:
            try:
                from . import ollama_client
                lines = current_source.splitlines()
                start = max(0, (dep.import_line or 1) - 3)
                snippet = "\n".join(lines[start:start + 20])
                fixed = ollama_client.suggest_code_fix(
                    snippet, dep.name, insight.api_changes, language, model=ollama_model
                )
                if fixed:
                    insight.codemod_changes.append(
                        f"Ollama suggested rewrite for lines around {dep.import_line or '?'}"
                    )
            except Exception:
                pass

        insights.append(insight)

    result.insights = insights

    # Write codemod changes back to file
    if current_source != source and result.file_path.exists():
        result.file_path.write_text(current_source, encoding="utf-8")


def analyze_file(
    file_path: Path,
    offline: bool = False,
    deep: bool = False,
) -> AnalysisResult:
    """Analyze a single file for dependency issues.

    Args:
        offline: Skip all network requests.
        deep: Enable Ollama-powered changelog analysis + code suggestions.
    """
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
        except Exception:
            pass

    for dep in deps:
        norm = analyzer.normalize_package_name(dep.name)
        for key in (norm, dep.name.lower(), dep.name):
            if key in manifest:
                dep.in_manifest = True
                dep.current_version = manifest[key]
                break

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

    for dep in deps:
        has_breaking, notes = _check_breaking(dep, analyzer.language)
        dep.has_breaking_changes = has_breaking
        dep.breaking_change_notes = notes

    result = AnalysisResult(
        file_path=file_path,
        language=analyzer.language,
        dependencies=deps,
        manifest_path=manifest_path,
    )

    if not offline:
        _enrich_with_insights(result, source, analyzer.language, deep=deep)

    return result


def analyze_directory(
    directory: Path,
    offline: bool = False,
    recursive: bool = True,
    deep: bool = False,
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
        parts = file_path.parts
        skip_dirs = {
            "node_modules", ".venv", "venv", "__pycache__", ".git",
            ".tox", "dist", "build", "target", ".mypy_cache",
        }
        if any(part in skip_dirs for part in parts):
            continue

        result = analyze_file(file_path, offline=offline, deep=deep)
        results.append(result)

    return results
