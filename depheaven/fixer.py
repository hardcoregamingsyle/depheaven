"""Apply fixes to manifests and source files."""

from pathlib import Path
from typing import Optional

from .languages import get_analyzer
from .languages.base import AnalysisResult, DependencyInfo


def build_updates(result: AnalysisResult) -> dict[str, str]:
    return compute_updates(result)


def compute_updates(result: AnalysisResult) -> dict[str, str]:
    """Return {package_name: latest_version} for deps that should be updated."""
    updates: dict[str, str] = {}
    for dep in result.dependencies:
        if dep.latest_version and dep.status in ("outdated", "breaking", "missing"):
            # For missing deps, we'd add them to the manifest at latest version
            updates[dep.name] = dep.latest_version
    return updates


def apply_fixes(
    result: AnalysisResult,
    selected_deps: Optional[list[str]] = None,
) -> dict[str, list[str]]:
    """
    Apply manifest updates for the given analysis result.

    Args:
        result: AnalysisResult from analyze_file()
        selected_deps: If provided, only fix these package names. Otherwise fix all.

    Returns:
        dict with keys 'updated', 'skipped', 'errors'
    """
    outcome: dict[str, list[str]] = {"updated": [], "skipped": [], "errors": []}

    analyzer = get_analyzer(result.file_path)
    if analyzer is None:
        outcome["errors"].append(f"No analyzer for {result.file_path}")
        return outcome

    if not result.manifest_path:
        outcome["errors"].append(
            f"No manifest file found for {result.file_path}. "
            "Cannot apply fixes without a package manifest."
        )
        return outcome

    updates = compute_updates(result)
    if selected_deps:
        updates = {k: v for k, v in updates.items() if k in selected_deps}

    if not updates:
        outcome["skipped"].append("No updates to apply")
        return outcome

    try:
        analyzer.apply_manifest_fixes(result.manifest_path, updates)
        for pkg, ver in updates.items():
            outcome["updated"].append(f"{pkg} → {ver}")
    except Exception as e:
        outcome["errors"].append(f"Failed to update manifest: {e}")

    return outcome
