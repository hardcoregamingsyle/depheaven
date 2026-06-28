"""Fetch changelogs from public APIs — no auth required."""

import json
import re
import urllib.request
from typing import Optional

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


def _get(url: str, timeout: int = 10) -> Optional[dict | list | str]:
    try:
        if _HAS_HTTPX:
            r = httpx.get(url, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "depheaven-cli/1.0"})
            if r.status_code == 200:
                ct = r.headers.get("content-type", "")
                return r.json() if "json" in ct else r.text
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "depheaven-cli/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(data)
                except Exception:
                    return data
    except Exception:
        return None


# ── PyPI ─────────────────────────────────────────────────────────────────────

def pypi_changelog(package: str, from_version: str, to_version: str) -> Optional[str]:
    """
    Return a best-effort changelog string for a PyPI package between two versions.
    Tries:
      1. PyPI JSON API description (often contains CHANGELOG section)
      2. GitHub releases if repo URL is in PyPI metadata
    """
    data = _get(f"https://pypi.org/pypi/{package}/json")
    if not isinstance(data, dict):
        return None

    info = data.get("info", {})

    # Try to find GitHub repo from project_urls
    repo_url = _find_github_repo(info)

    changelog = None

    if repo_url:
        changelog = _github_releases_text(repo_url, from_version, to_version)

    # Fallback: pull CHANGELOG block out of the long description
    if not changelog:
        desc = info.get("description", "")
        changelog = _extract_changelog_section(desc, from_version, to_version)

    return changelog


def _find_github_repo(info: dict) -> Optional[str]:
    urls: dict = info.get("project_urls") or {}
    candidates = list(urls.values()) + [
        info.get("home_page", ""),
        info.get("bugtrack_url", "") or "",
    ]
    for url in candidates:
        if not url:
            continue
        m = re.search(r"github\.com/([^/]+/[^/\s#?]+)", url)
        if m:
            return m.group(1).rstrip(".git")
    return None


def _extract_changelog_section(text: str, from_ver: str, to_ver: str) -> Optional[str]:
    """Pull the relevant section(s) from a CHANGELOG embedded in long_description."""
    if not text:
        return None
    # Find lines between from_ver and to_ver headings (version headers in markdown/rst)
    lines = text.splitlines()
    relevant: list[str] = []
    in_range = False

    # Match lines like "## 2.1.0", "2.1.0 (2023-01-01)", "Version 2.1.0"
    ver_pat = re.compile(r"[\#\*]?\s*v?([\d]+\.[\d]+[\d.]*)")

    for line in lines:
        m = ver_pat.search(line)
        if m:
            ver = m.group(1)
            if _ver_gte(ver, from_ver) and _ver_lte(ver, to_ver):
                in_range = True
            elif in_range:
                break  # past our range
        if in_range:
            relevant.append(line)

    return "\n".join(relevant).strip() or None


# ── GitHub ───────────────────────────────────────────────────────────────────

def github_releases_changelog(owner_repo: str, from_version: str, to_version: str) -> Optional[str]:
    return _github_releases_text(owner_repo, from_version, to_version)


def _github_releases_text(owner_repo: str, from_ver: str, to_ver: str) -> Optional[str]:
    """Fetch GitHub releases (public API, 60 req/hr unauthenticated) and extract relevant notes."""
    url = f"https://api.github.com/repos/{owner_repo}/releases?per_page=30"
    data = _get(url)
    if not isinstance(data, list):
        return None

    parts: list[str] = []
    for release in data:
        tag = release.get("tag_name", "").lstrip("vV")
        # include releases between from_ver and to_ver
        if _ver_gte(tag, from_ver) and _ver_lte(tag, to_ver):
            name = release.get("name") or release.get("tag_name", "")
            body = release.get("body", "").strip()
            if body:
                parts.append(f"### {name}\n{body}")

    return "\n\n".join(parts) if parts else None


# ── npm ──────────────────────────────────────────────────────────────────────

def npm_changelog(package: str, from_version: str, to_version: str) -> Optional[str]:
    """Fetch npm package metadata and try to get changelog from its GitHub repo."""
    encoded = package.replace("/", "%2F")
    data = _get(f"https://registry.npmjs.org/{encoded}")
    if not isinstance(data, dict):
        return None

    repo_url = _npm_repo_url(data)
    if not repo_url:
        return None

    m = re.search(r"github\.com/([^/]+/[^/\s#?]+)", repo_url)
    if not m:
        return None
    owner_repo = m.group(1).rstrip(".git")
    return _github_releases_text(owner_repo, from_version, to_version)


def _npm_repo_url(data: dict) -> Optional[str]:
    repo = data.get("repository", {})
    if isinstance(repo, str):
        return repo
    if isinstance(repo, dict):
        url = repo.get("url", "")
        return url.replace("git+", "").replace("git://", "https://")
    return None


# ── version comparison helpers ────────────────────────────────────────────────

def _parse_ver(v: str) -> tuple[int, ...]:
    v = re.sub(r"[^0-9.]", "", v)
    try:
        return tuple(int(x) for x in v.split(".") if x)
    except Exception:
        return (0,)


def _ver_gte(a: str, b: str) -> bool:
    return _parse_ver(a) >= _parse_ver(b)


def _ver_lte(a: str, b: str) -> bool:
    return _parse_ver(a) <= _parse_ver(b)
