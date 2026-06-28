"""
Parse raw changelog text (markdown/rst/plain) without any LLM.

Extracts structured breaking-change information by understanding
the conventions developers actually use when writing changelogs:
  - "BREAKING CHANGE:" / "BREAKING:" prefixes (Conventional Commits)
  - "### Breaking Changes" sections in markdown
  - "Removed", "Deprecated", "Renamed", "Migration" headings
  - Diff blocks showing old → new API
  - GitHub release notes patterns
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedChange:
    kind: str           # "breaking" | "removed" | "deprecated" | "renamed" | "added" | "fixed"
    description: str
    old_api: Optional[str] = None   # e.g. ".dict()"
    new_api: Optional[str] = None   # e.g. ".model_dump()"
    affected_symbol: Optional[str] = None   # the function/class name affected
    line_ref: Optional[int] = None


@dataclass
class ParsedChangelog:
    package: str
    from_version: str
    to_version: str
    breaking: list[ParsedChange] = field(default_factory=list)
    removed: list[ParsedChange] = field(default_factory=list)
    deprecated: list[ParsedChange] = field(default_factory=list)
    renamed: list[ParsedChange] = field(default_factory=list)
    migration_hints: list[str] = field(default_factory=list)
    raw_sections: list[str] = field(default_factory=list)

    @property
    def has_breaking(self) -> bool:
        return bool(self.breaking or self.removed or self.renamed)

    @property
    def all_changes(self) -> list[ParsedChange]:
        return self.breaking + self.removed + self.deprecated + self.renamed


# ── Section detection ──────────────────────────────────────────────────────────

_BREAKING_SECTION = re.compile(
    r"#+\s*(breaking\s+changes?|breaking|incompatible\s+changes?|migration|upgrade\s+guide)",
    re.IGNORECASE,
)
_REMOVED_SECTION = re.compile(r"#+\s*(removed|deletions?|dropped)", re.IGNORECASE)
_DEPRECATED_SECTION = re.compile(r"#+\s*(deprecated|deprecations?)", re.IGNORECASE)
_ANY_HEADING = re.compile(r"^#+\s+", re.MULTILINE)

# ── Line-level patterns ────────────────────────────────────────────────────────

_BREAKING_PREFIX = re.compile(
    r"^[\*\-\s]*(BREAKING[\s_]CHANGE[S]?|BREAKING|💥|⚠️?)\s*:?\s*(.+)",
    re.IGNORECASE,
)
_REMOVED_LINE = re.compile(
    r"[\*\-]\s+(?:removed?|deleted?|dropped?)\s+[`'\"]?([\w\.\(\)_]+)[`'\"]?",
    re.IGNORECASE,
)
_DEPRECATED_LINE = re.compile(
    r"[\*\-]\s+(?:deprecat\w+)\s+[`'\"]?([\w\.\(\)_]+)[`'\"]?",
    re.IGNORECASE,
)
_RENAMED_LINE = re.compile(
    r"[\*\-]?\s*[`'\"]?([\w\.\(\)_]+)[`'\"]?\s+(?:is\s+)?(?:renamed?(?:\s+to)?|→|->|now\s+called?)\s+[`'\"]?([\w\.\(\)_]+)[`'\"]?",
    re.IGNORECASE,
)
_USE_INSTEAD = re.compile(
    r"use\s+[`'\"]?([\w\.\(\)_]+)[`'\"]?\s+instead",
    re.IGNORECASE,
)
_DIFF_MINUS = re.compile(r"^-\s+(.+)")  # diff blocks: - old line
_DIFF_PLUS = re.compile(r"^\+\s+(.+)")   # diff blocks: + new line

# ── Backtick/inline-code symbol extraction ─────────────────────────────────────

_BACKTICK_SYMBOL = re.compile(r"`([\w\.\(\)_:]+)`")


def parse(text: str, package: str, from_version: str, to_version: str) -> ParsedChangelog:
    result = ParsedChangelog(package=package, from_version=from_version, to_version=to_version)
    if not text:
        return result

    lines = text.splitlines()
    sections = _split_into_sections(lines)

    for section_heading, section_lines in sections:
        heading_lower = section_heading.lower()
        is_breaking = bool(_BREAKING_SECTION.search(section_heading))
        is_removed = bool(_REMOVED_SECTION.search(section_heading))
        is_deprecated = bool(_DEPRECATED_SECTION.search(section_heading))

        if is_breaking or is_removed or is_deprecated:
            result.raw_sections.append(f"## {section_heading}\n" + "\n".join(section_lines))

        _parse_section_lines(
            section_lines,
            result,
            section_is_breaking=is_breaking,
            section_is_removed=is_removed,
            section_is_deprecated=is_deprecated,
        )

    # Scan every line for BREAKING CHANGE: prefix regardless of section
    for line in lines:
        m = _BREAKING_PREFIX.match(line)
        if m:
            desc = m.group(2).strip()
            change = _make_change("breaking", desc, line)
            if not _already_have(result.breaking, desc):
                result.breaking.append(change)

    # Extract migration hints (sentences containing "migrate", "upgrade", "instead")
    full = " ".join(lines)
    for sent in re.split(r"[.!?\n]", full):
        sent = sent.strip()
        if re.search(r"\b(migrate|migration|upgrade\s+guide|instead|replace\s+with)\b", sent, re.I):
            if len(sent) > 20:
                result.migration_hints.append(sent)

    return result


def _split_into_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split changelog text into (heading, body_lines) pairs."""
    sections: list[tuple[str, list[str]]] = []
    current_heading = "preamble"
    current_body: list[str] = []

    for line in lines:
        m = _ANY_HEADING.match(line)
        if m:
            sections.append((current_heading, current_body))
            current_heading = line.lstrip("# ").strip()
            current_body = []
        else:
            current_body.append(line)

    sections.append((current_heading, current_body))
    return sections


def _parse_section_lines(
    lines: list[str],
    result: ParsedChangelog,
    section_is_breaking: bool,
    section_is_removed: bool,
    section_is_deprecated: bool,
) -> None:
    in_diff = False
    diff_minus: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track code diff blocks
        if stripped.startswith("```diff"):
            in_diff = True
            diff_minus = None
            continue
        if stripped.startswith("```") and in_diff:
            in_diff = False
            diff_minus = None
            continue
        if in_diff:
            mm = _DIFF_MINUS.match(stripped)
            mp = _DIFF_PLUS.match(stripped)
            if mm:
                diff_minus = mm.group(1).strip()
            elif mp and diff_minus:
                new_code = mp.group(1).strip()
                result.renamed.append(ParsedChange(
                    kind="renamed",
                    description=f"`{diff_minus}` → `{new_code}`",
                    old_api=diff_minus,
                    new_api=new_code,
                ))
                diff_minus = None
            continue

        # Renamed / use-instead patterns
        m = _RENAMED_LINE.search(stripped)
        if m:
            old, new = m.group(1), m.group(2)
            change = ParsedChange(
                kind="renamed",
                description=f"`{old}` renamed to `{new}`",
                old_api=old,
                new_api=new,
                affected_symbol=old,
            )
            if not _already_have(result.renamed, change.description):
                result.renamed.append(change)
            continue

        # Removed lines
        m = _REMOVED_LINE.search(stripped)
        if m or section_is_removed:
            sym = m.group(1) if m else _first_symbol(stripped)
            if sym and len(stripped) > 5:
                change = _make_change("removed", stripped, line, affected_symbol=sym)
                # check use-instead
                ui = _USE_INSTEAD.search(stripped)
                if ui:
                    change.new_api = ui.group(1)
                if not _already_have(result.removed, stripped[:60]):
                    result.removed.append(change)
            continue

        # Deprecated lines
        m = _DEPRECATED_LINE.search(stripped)
        if m or section_is_deprecated:
            sym = m.group(1) if m else _first_symbol(stripped)
            if sym and len(stripped) > 5:
                change = _make_change("deprecated", stripped, line, affected_symbol=sym)
                ui = _USE_INSTEAD.search(stripped)
                if ui:
                    change.new_api = ui.group(1)
                if not _already_have(result.deprecated, stripped[:60]):
                    result.deprecated.append(change)
            continue

        # Everything in a breaking section
        if section_is_breaking and stripped and not stripped.startswith("#"):
            change = _make_change("breaking", stripped, line)
            if not _already_have(result.breaking, stripped[:60]):
                result.breaking.append(change)


def _make_change(kind: str, desc: str, raw_line: str, affected_symbol: Optional[str] = None) -> ParsedChange:
    # Try to pull old/new API from the description
    old_api = new_api = None
    m = _RENAMED_LINE.search(desc)
    if m:
        old_api, new_api = m.group(1), m.group(2)
    elif not affected_symbol:
        syms = _BACKTICK_SYMBOL.findall(desc)
        if syms:
            affected_symbol = syms[0]

    return ParsedChange(
        kind=kind,
        description=desc[:200],
        old_api=old_api,
        new_api=new_api,
        affected_symbol=affected_symbol,
    )


def _first_symbol(text: str) -> Optional[str]:
    syms = _BACKTICK_SYMBOL.findall(text)
    return syms[0] if syms else None


def _already_have(lst: list[ParsedChange], key: str) -> bool:
    key = key.lower()[:60]
    return any(key in c.description.lower() for c in lst)


# ── Convenience: turn ParsedChangelog into human-readable lines ───────────────

def format_parsed(parsed: ParsedChangelog) -> list[str]:
    out: list[str] = []
    if parsed.breaking:
        out.append("Breaking changes:")
        for c in parsed.breaking[:8]:
            out.append(f"  • {c.description}")
    if parsed.removed:
        out.append("Removed:")
        for c in parsed.removed[:6]:
            s = f"  • {c.description}"
            if c.new_api:
                s += f" → use `{c.new_api}`"
            out.append(s)
    if parsed.deprecated:
        out.append("Deprecated:")
        for c in parsed.deprecated[:4]:
            s = f"  • {c.description}"
            if c.new_api:
                s += f" → use `{c.new_api}`"
            out.append(s)
    if parsed.renamed:
        out.append("Renamed:")
        for c in parsed.renamed[:6]:
            out.append(f"  • {c.description}")
    if parsed.migration_hints:
        out.append("Migration notes:")
        for h in parsed.migration_hints[:3]:
            out.append(f"  → {h}")
    return out
