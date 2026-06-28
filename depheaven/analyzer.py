"""Main analysis orchestrator for DepHeaven."""

from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .languages import get_analyzer
from .languages.base import AnalysisResult, DependencyInfo, DependencyInsight
from .registry import pypi_latest, npm_latest


BREAKING_CHANGES: dict[tuple[str, str], str] = {
    ("django", "2"): "Django 2→3: url() removed, use path()/re_path()",
    ("django", "3"): "Django 3→4: ugettext() removed, use gettext()",
    ("django", "4"): "Django 4→5: index_together deprecated; CSRF changes",
    ("flask", "1"): "Flask 1→2: before_first_request removed",
    ("flask", "2"): "Flask 2→3: FLASK_ENV removed; helpers restructured",
    ("sqlalchemy", "1"): "SQLAlchemy 1→2: Session.execute() API changed; Query removed",
    ("pydantic", "1"): "Pydantic v1→v2: .dict()→.model_dump(); validators rewritten",
    ("numpy", "1"): "NumPy 1→2: np.bool/np.int/np.float aliases removed",
    ("pandas", "1"): "Pandas 1→2: DataFrame.append() removed; use pd.concat()",
    ("celery", "4"): "Celery 4→5: task_always_eager removed",
    ("pytest", "6"): "pytest 6→7: warns() match required",
    ("pytest", "7"): "pytest 7→8: --strict removed; use --strict-markers",
    ("react", "16"): "React 16→17: lifecycle method renames (UNSAFE_ prefix)",
    ("react", "17"): "React 17→18: ReactDOM.render() → createRoot().render()",
    ("react", "18"): "React 18→19: forwardRef deprecated; ref as prop",
    ("webpack", "4"): "Webpack 4→5: asset modules built-in; require.extensions removed",
    ("express", "4"): "Express 4→5: app.del() → app.delete(); req.param() removed",
    ("vue", "2"): "Vue 2→3: new Vue() → createApp(); $on/$off removed",
    ("axios", "0"): "Axios 0.x→1.x: Cancel → CanceledError; CancelToken deprecated",
    ("next", "12"): "Next.js 12→13: Image layout prop removed; Link no longer needs <a>",
    ("next", "13"): "Next.js 13→14: experimental.appDir removed; @next/font moved",
    ("lodash", "3"): "Lodash 3→4: _.pluck/_.any/_.all/_.contains removed",
    ("tailwindcss", "2"): "Tailwind 2→3: purge: → content:; JIT is now default",
    ("marshmallow", "2"): "marshmallow 2→3: dump/load no longer return (data,errors) tuples",
    ("attrs", "19"): "attrs 19→20: attr.ib() → attr.field(); @attr.s → @attr.define",
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
            return True, f"Major version bump {cur_major}→{lat_major}: review changelog"
    except (ValueError, IndexError):
        pass
    return False, None


def _enrich_with_insights(
    result: AnalysisResult,
    source: str,
    language: str,
) -> None:
    """
    For each dep that has an upgrade available:
    1. Fetch real changelog from PyPI/GitHub (public API, no auth)
    2. Parse it with changelog_parser to extract structured breaking changes
    3. Scan the source file to find actual usages of affected APIs
    4. Apply AST/regex codemods where possible
    5. Record precise, line-level findings
    """
    from .changelog import pypi_changelog, npm_changelog
    from .changelog_parser import parse as parse_changelog, format_parsed
    from .codemods import apply_codemods
    from .usage_scanner import scan_usages, match_usages_to_changes

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

        # 1. Fetch changelog
        changelog_text: Optional[str] = None
        try:
            if "python" in language.lower():
                changelog_text = pypi_changelog(dep.name, dep.current_version, dep.latest_version)
            else:
                changelog_text = npm_changelog(dep.name, dep.current_version, dep.latest_version)
        except Exception:
            pass

        # 2. Parse changelog structurally (no LLM)
        if changelog_text:
            try:
                parsed = parse_changelog(
                    changelog_text, dep.name,
                    dep.current_version, dep.latest_version,
                )
                formatted = format_parsed(parsed)
                if formatted:
                    insight.changelog_summary = "\n".join(formatted)

                all_changes = parsed.all_changes
                insight.api_changes = [c.description for c in all_changes[:10]]
                insight.migration_steps = parsed.migration_hints[:5]

                # Escalate breaking flag if changelog confirms it
                if parsed.has_breaking and not dep.has_breaking_changes:
                    dep.has_breaking_changes = True
                    if not dep.breaking_change_notes and insight.api_changes:
                        dep.breaking_change_notes = insight.api_changes[0]

                # 3. Scan source for actual usages of affected APIs
                if all_changes:
                    usages = scan_usages(current_source, language, dep.name)
                    hits = match_usages_to_changes(usages, all_changes)
                    for usage, change in hits:
                        insight.codemod_changes.append(
                            f"[line {usage.line}] You use `{usage.symbol}` — "
                            f"{change.description}"
                            + (f" → use `{change.new_api}`" if change.new_api else "")
                        )
            except Exception:
                pass

        # 4. Apply AST/regex codemods
        try:
            new_source, codemod_changes = apply_codemods(
                current_source, language, dep.name,
                dep.current_version, dep.latest_version,
            )
            if codemod_changes:
                insight.codemod_changes = codemod_changes + insight.codemod_changes
                current_source = new_source
        except Exception:
            pass

        insights.append(insight)

    result.insights = insights

    # Write codemod changes back to file if anything changed
    if current_source != source and result.file_path.exists():
        result.file_path.write_text(current_source, encoding="utf-8")


def analyze_file(
    file_path: Path,
    offline: bool = False,
) -> AnalysisResult:
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
        _enrich_with_insights(result, source, analyzer.language)

    return result


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
        parts = file_path.parts
        skip_dirs = {
            "node_modules", ".venv", "venv", "__pycache__", ".git",
            ".tox", "dist", "build", "target", ".mypy_cache",
        }
        if any(part in skip_dirs for part in parts):
            continue
        results.append(analyze_file(file_path, offline=offline))

    return results
