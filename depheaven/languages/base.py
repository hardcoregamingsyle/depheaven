"""Abstract base class for language-specific dependency analyzers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DependencyInfo:
    """Represents a single dependency found in a source file."""
    name: str
    imported_name: str  # as it appears in the source (may differ from package name)
    current_version: Optional[str] = None  # version in manifest
    latest_version: Optional[str] = None   # latest available
    in_manifest: bool = False
    has_breaking_changes: bool = False
    breaking_change_notes: Optional[str] = None
    import_line: Optional[int] = None       # line number in source file
    import_statement: Optional[str] = None  # original import text

    @property
    def is_outdated(self) -> bool:
        if not self.current_version or not self.latest_version:
            return False
        return self.current_version != self.latest_version

    @property
    def status(self) -> str:
        if not self.in_manifest:
            return "missing"
        if self.has_breaking_changes:
            return "breaking"
        if self.is_outdated:
            return "outdated"
        return "ok"


@dataclass
class DependencyInsight:
    """Changelog-derived intelligence for a single dependency upgrade."""
    package: str
    from_version: str
    to_version: str
    changelog_summary: Optional[str] = None      # human-readable summary
    api_changes: list[str] = field(default_factory=list)
    migration_steps: list[str] = field(default_factory=list)
    codemod_changes: list[str] = field(default_factory=list)  # changes already applied
    ollama_used: bool = False


@dataclass
class AnalysisResult:
    """Result of analyzing a file or directory."""
    file_path: Path
    language: str
    dependencies: list[DependencyInfo] = field(default_factory=list)
    manifest_path: Optional[Path] = None
    errors: list[str] = field(default_factory=list)
    insights: list[DependencyInsight] = field(default_factory=list)


class BaseAnalyzer(ABC):
    """Abstract base class for language-specific analyzers."""

    @property
    @abstractmethod
    def language(self) -> str:
        """Return the language name."""
        ...

    @property
    @abstractmethod
    def extensions(self) -> list[str]:
        """Return list of file extensions this analyzer handles."""
        ...

    @abstractmethod
    def extract_imports(self, source: str) -> list[DependencyInfo]:
        """Parse source code and return list of dependencies found."""
        ...

    @abstractmethod
    def find_manifest(self, file_path: Path) -> Optional[Path]:
        """Find the dependency manifest file for this source file."""
        ...

    @abstractmethod
    def parse_manifest(self, manifest_path: Path) -> dict[str, str]:
        """Parse manifest and return {package_name: version} dict."""
        ...

    @abstractmethod
    def apply_manifest_fixes(
        self,
        manifest_path: Path,
        updates: dict[str, str],
    ) -> None:
        """Write updated versions back to the manifest file."""
        ...

    def normalize_package_name(self, name: str) -> str:
        """Normalize package name for comparison (override as needed)."""
        return name.lower().replace("-", "_").replace(".", "_")
