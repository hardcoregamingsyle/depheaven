"""
Dependency graph builder.

Fetches transitive dependencies from PyPI and npm registries
(public APIs, no auth) and builds a full directed graph so we
can detect version conflicts that only appear several levels deep.
"""

import json
import re
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get_json(url: str, timeout: int = 10) -> Optional[dict]:
    try:
        if _HAS_HTTPX:
            r = httpx.get(url, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "depheaven-cli/1.0"})
            if r.status_code == 200:
                return r.json()
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "depheaven-cli/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
    except Exception:
        return None


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Requirement:
    """A version constraint one package places on another."""
    name: str           # required package
    specifier: str      # e.g. ">=1.0,<2.0"
    extras: list[str] = field(default_factory=list)
    marker: Optional[str] = None    # environment marker, e.g. 'python_version >= "3.8"'

    def __str__(self) -> str:
        s = self.name
        if self.specifier:
            s += self.specifier
        if self.marker:
            s += f" ; {self.marker}"
        return s


@dataclass
class PackageNode:
    name: str
    version: str                        # resolved version
    requires: list[Requirement] = field(default_factory=list)
    fetch_error: Optional[str] = None


@dataclass
class DependencyGraph:
    root_packages: dict[str, str]           # {name: version} from your manifest
    nodes: dict[str, PackageNode] = field(default_factory=dict)   # key = name_lower
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (from, to, specifier)

    def add_node(self, node: PackageNode) -> None:
        self.nodes[node.name.lower()] = node

    def get_node(self, name: str) -> Optional[PackageNode]:
        return self.nodes.get(name.lower())


@dataclass
class ConflictChain:
    """A detected version conflict in the dependency graph."""
    package: str                    # the package with conflicting requirements
    requirements: list[tuple[str, str, str]]  # [(requirer, specifier, resolved_version)]
    conflict_type: str              # "incompatible_specifiers" | "missing" | "cycle"
    path_a: list[str]               # dependency path leading to first requirement
    path_b: list[str]               # dependency path leading to conflicting requirement
    suggestion: Optional[str] = None


# ── PyPI graph builder ────────────────────────────────────────────────────────

_PYPI_STDLIB = {
    "python", "setuptools", "pip", "wheel", "distribute",
    "pkg_resources", "importlib_metadata", "zipp",
}

def _norm_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _parse_requirement_str(req_str: str) -> Optional[Requirement]:
    """Parse a PEP 508 requirement string like 'requests>=2.0,<3.0 ; python_version>="3.7"'."""
    req_str = req_str.strip()
    if not req_str:
        return None
    # Split off marker
    marker = None
    if " ; " in req_str:
        req_str, marker = req_str.split(" ; ", 1)

    # Package name + extras
    m = re.match(r"^([A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)"
                 r"(\[([^\]]+)\])?"
                 r"\s*(.*)", req_str.strip())
    if not m:
        return None

    name = m.group(1)
    extras_str = m.group(4) or ""
    specifier = m.group(5).strip()

    return Requirement(
        name=name,
        specifier=specifier,
        extras=[e.strip() for e in extras_str.split(",") if e.strip()],
        marker=marker,
    )


def fetch_pypi_metadata(package: str, version: Optional[str] = None) -> Optional[PackageNode]:
    """Fetch a package's dependencies from PyPI."""
    if _norm_pypi(package) in _PYPI_STDLIB:
        return PackageNode(name=package, version=version or "builtin")

    url = (f"https://pypi.org/pypi/{package}/{version}/json"
           if version else f"https://pypi.org/pypi/{package}/json")
    data = _get_json(url)
    if not data:
        return PackageNode(name=package, version=version or "unknown",
                           fetch_error="not found on PyPI")

    info = data.get("info", {})
    resolved_version = version or info.get("version", "unknown")
    raw_requires = info.get("requires_dist") or []

    requires: list[Requirement] = []
    for r in raw_requires:
        req = _parse_requirement_str(r)
        if req and _norm_pypi(req.name) not in _PYPI_STDLIB:
            requires.append(req)

    return PackageNode(name=_norm_pypi(package), version=resolved_version, requires=requires)


# ── npm graph builder ─────────────────────────────────────────────────────────

_NPM_BUILTIN = {
    "node", "npm", "yarn", "pnpm",
}

def _norm_npm(name: str) -> str:
    return name.lower()


def fetch_npm_metadata(package: str, version: Optional[str] = None) -> Optional[PackageNode]:
    """Fetch a package's dependencies from npm registry."""
    if package.lower() in _NPM_BUILTIN:
        return PackageNode(name=package, version=version or "builtin")

    encoded = package.replace("/", "%2F")
    # Strip semver operators from version for URL
    clean_ver = re.sub(r"[^0-9.]", "", version or "").strip(".") if version else None

    url = (f"https://registry.npmjs.org/{encoded}/{clean_ver}"
           if clean_ver else f"https://registry.npmjs.org/{encoded}/latest")
    data = _get_json(url)
    if not data:
        return PackageNode(name=package, version=version or "unknown",
                           fetch_error="not found on npm")

    resolved_version = data.get("version", version or "unknown")
    raw_deps: dict = data.get("dependencies", {}) or {}
    peer_deps: dict = data.get("peerDependencies", {}) or {}

    requires = [
        Requirement(name=dep, specifier=spec)
        for dep, spec in {**raw_deps, **peer_deps}.items()
        if dep.lower() not in _NPM_BUILTIN
    ]

    return PackageNode(name=_norm_npm(package), version=resolved_version, requires=requires)


# ── Graph builder (BFS, bounded depth) ────────────────────────────────────────

def build_graph(
    root_packages: dict[str, str],
    ecosystem: str = "python",     # "python" | "npm"
    max_depth: int = 4,
    max_workers: int = 12,
) -> DependencyGraph:
    """
    Build a full dependency graph starting from root_packages.

    Args:
        root_packages: {name: installed_version} from the manifest
        ecosystem: "python" or "npm"
        max_depth: how many levels deep to recurse
        max_workers: parallel fetch threads
    """
    fetch = fetch_pypi_metadata if ecosystem == "python" else fetch_npm_metadata
    norm = _norm_pypi if ecosystem == "python" else _norm_npm

    graph = DependencyGraph(root_packages=root_packages)
    visited: set[str] = set()
    # Queue: (package_name, version, depth)
    queue: list[tuple[str, Optional[str], int]] = [
        (name, ver, 0) for name, ver in root_packages.items()
    ]

    while queue:
        # Fetch this level in parallel
        batch = queue[:]
        queue = []

        def fetch_one(item: tuple) -> Optional[PackageNode]:
            name, ver, depth = item
            key = norm(name)
            if key in visited:
                return None
            visited.add(key)
            return fetch(name, ver)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fetch_one, item): item for item in batch}
            for future in as_completed(futures):
                item = futures[future]
                name, ver, depth = item
                try:
                    node = future.result()
                except Exception:
                    node = None

                if node is None:
                    continue

                graph.add_node(node)

                if depth < max_depth:
                    for req in node.requires:
                        dep_key = norm(req.name)
                        if dep_key not in visited:
                            # Extract a concrete version from the specifier if possible
                            pin = _extract_pin(req.specifier)
                            queue.append((req.name, pin, depth + 1))
                        # Always record the edge
                        graph.edges.append((norm(name), dep_key, req.specifier))

    return graph


def _extract_pin(specifier: str) -> Optional[str]:
    """Try to extract a single concrete version from a specifier like '==1.2.3'."""
    m = re.match(r"^==\s*([\d.]+)", specifier.strip())
    return m.group(1) if m else None


# ── Version conflict detection ─────────────────────────────────────────────────

def detect_conflicts(graph: DependencyGraph, ecosystem: str = "python") -> list[ConflictChain]:
    """
    Find packages that are required by multiple dependents with
    incompatible version constraints.
    """
    # Collect all requirements pointing at each package
    # {target_pkg: [(requirer, specifier)]}
    requirements_for: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (src, dst, spec) in graph.edges:
        if spec:  # only care about versioned constraints
            requirements_for[dst].append((src, spec))

    conflicts: list[ConflictChain] = []

    for pkg, reqs in requirements_for.items():
        if len(reqs) < 2:
            continue

        # Check every pair for incompatibility
        for i in range(len(reqs)):
            for j in range(i + 1, len(reqs)):
                src_a, spec_a = reqs[i]
                src_b, spec_b = reqs[j]

                if not _specs_compatible(spec_a, spec_b, ecosystem):
                    path_a = _find_path(graph, list(graph.root_packages.keys()), src_a)
                    path_b = _find_path(graph, list(graph.root_packages.keys()), src_b)

                    node = graph.get_node(pkg)
                    resolved = node.version if node else "unknown"

                    suggestion = _suggest_resolution(pkg, spec_a, spec_b, src_a, src_b, ecosystem)

                    conflicts.append(ConflictChain(
                        package=pkg,
                        requirements=[
                            (src_a, spec_a, resolved),
                            (src_b, spec_b, resolved),
                        ],
                        conflict_type="incompatible_specifiers",
                        path_a=path_a + [src_a, pkg],
                        path_b=path_b + [src_b, pkg],
                        suggestion=suggestion,
                    ))

    # Also check: packages your manifest pins at a version that conflicts
    # with what their transitive dependencies actually need
    _check_manifest_vs_transitive(graph, requirements_for, conflicts, ecosystem)

    # Deduplicate (same package, same pair of requirers)
    seen: set[tuple] = set()
    unique: list[ConflictChain] = []
    for c in conflicts:
        key = (c.package, frozenset((r[0], r[1]) for r in c.requirements))
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


def _check_manifest_vs_transitive(
    graph: DependencyGraph,
    requirements_for: dict[str, list[tuple[str, str]]],
    conflicts: list[ConflictChain],
    ecosystem: str,
) -> None:
    """Check if your pinned versions satisfy what transitive deps require."""
    for pkg_name, pinned_version in graph.root_packages.items():
        norm_name = pkg_name.lower().replace("-", "_")
        reqs = requirements_for.get(norm_name, [])
        for requirer, spec in reqs:
            if not _version_satisfies(pinned_version, spec, ecosystem):
                path = _find_path(graph, list(graph.root_packages.keys()), requirer)
                conflicts.append(ConflictChain(
                    package=norm_name,
                    requirements=[
                        ("your manifest", f"=={pinned_version}", pinned_version),
                        (requirer, spec, pinned_version),
                    ],
                    conflict_type="manifest_pin_conflict",
                    path_a=[norm_name],
                    path_b=path + [requirer, norm_name],
                    suggestion=(
                        f"`{requirer}` requires `{norm_name}{spec}` but your "
                        f"manifest pins it at `{pinned_version}`. "
                        f"Try upgrading `{norm_name}` or downgrading `{requirer}`."
                    ),
                ))


def _find_path(graph: DependencyGraph, roots: list[str], target: str) -> list[str]:
    """BFS to find a path from any root to `target` in the graph."""
    # Build reverse adjacency for BFS
    norm_target = target.lower().replace("-", "_")
    norm_roots = [r.lower().replace("-", "_") for r in roots]

    from collections import deque
    queue: deque[list[str]] = deque()
    for r in norm_roots:
        queue.append([r])
    visited: set[str] = set(norm_roots)

    # Build adjacency
    adj: dict[str, list[str]] = defaultdict(list)
    for src, dst, _ in graph.edges:
        adj[src].append(dst)

    while queue:
        path = queue.popleft()
        current = path[-1]
        if current == norm_target:
            return path[:-1]
        for neighbor in adj.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])

    return []


# ── Version constraint compatibility ──────────────────────────────────────────

def _specs_compatible(spec_a: str, spec_b: str, ecosystem: str) -> bool:
    """
    Return False if spec_a and spec_b cannot be satisfied simultaneously.
    Uses `packaging` for Python, simple semver logic for npm.
    """
    if not spec_a or not spec_b:
        return True  # unconstrained = compatible

    if ecosystem == "python":
        return _python_specs_compatible(spec_a, spec_b)
    else:
        return _npm_specs_compatible(spec_a, spec_b)


def _python_specs_compatible(spec_a: str, spec_b: str) -> bool:
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        sa = SpecifierSet(spec_a, prereleases=True)
        sb = SpecifierSet(spec_b, prereleases=True)

        # Test a range of plausible versions
        test_versions = [
            "0.1", "0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5",
            "4.0", "5.0", "6.0", "7.0", "8.0", "9.0", "10.0",
            "0.9.0", "1.0.0", "1.1.0", "1.2.0", "2.0.0", "2.1.0",
            "3.0.0", "4.0.0", "5.0.0",
        ]
        for v in test_versions:
            try:
                vv = Version(v)
                if vv in sa and vv in sb:
                    return True  # found a version satisfying both
            except Exception:
                continue

        # If no test version satisfied both, likely incompatible
        # But we can't be certain — say compatible to avoid false positives
        return _has_obvious_conflict(spec_a, spec_b)
    except Exception:
        return True  # can't determine → assume compatible


def _has_obvious_conflict(spec_a: str, spec_b: str) -> bool:
    """
    Detect obvious conflicts like:
      >=2.0  vs  <2.0
      ==1.x  vs  ==2.x
      <1.0   vs  >=2.0
    Returns True if they ARE compatible (no obvious conflict found).
    """
    def extract_bounds(spec: str) -> tuple[Optional[float], Optional[float]]:
        lo = hi = None
        for part in spec.split(","):
            part = part.strip()
            m = re.match(r"([><=!]+)\s*([\d.]+)", part)
            if not m:
                continue
            op, raw = m.group(1), m.group(2)
            try:
                v = float(".".join(raw.split(".")[:2]))
            except ValueError:
                continue
            if op in (">=", ">"):
                lo = v if lo is None else max(lo, v)
            elif op in ("<=", "<"):
                hi = v if hi is None else min(hi, v)
            elif op == "==":
                lo = hi = v
        return lo, hi

    lo_a, hi_a = extract_bounds(spec_a)
    lo_b, hi_b = extract_bounds(spec_b)

    # Conflict if max lower bound > min upper bound
    lo = max(x for x in [lo_a, lo_b] if x is not None) if any(x is not None for x in [lo_a, lo_b]) else None
    hi = min(x for x in [hi_a, hi_b] if x is not None) if any(x is not None for x in [hi_a, hi_b]) else None

    if lo is not None and hi is not None and lo > hi:
        return False  # conflict detected → NOT compatible
    return True  # no obvious conflict


def _npm_specs_compatible(spec_a: str, spec_b: str) -> bool:
    """Simplified semver range compatibility for npm."""
    # Extract major version requirements
    def major_range(spec: str) -> tuple[Optional[int], Optional[int]]:
        spec = spec.strip().lstrip("^~v")
        lo = hi = None
        parts = spec.split(".")
        try:
            v = int(parts[0])
            if spec_a.startswith("^") or spec_b.startswith("^"):
                lo = v; hi = v  # caret pins major
            elif spec_a.startswith("~") or spec_b.startswith("~"):
                lo = v; hi = v  # tilde pins major too at this level
            else:
                lo = v
        except (ValueError, IndexError):
            pass
        return lo, hi

    lo_a, hi_a = major_range(spec_a)
    lo_b, hi_b = major_range(spec_b)

    if (lo_a is not None and hi_b is not None and lo_a > hi_b):
        return False
    if (lo_b is not None and hi_a is not None and lo_b > hi_a):
        return False
    return True


def _version_satisfies(version: str, spec: str, ecosystem: str) -> bool:
    """Check if a concrete version satisfies a specifier."""
    if not spec or not version:
        return True
    version = version.lstrip("^~v>=<! ")
    if ecosystem == "python":
        try:
            from packaging.specifiers import SpecifierSet
            from packaging.version import Version
            return Version(version) in SpecifierSet(spec, prereleases=True)
        except Exception:
            return True
    else:
        # Simple npm check
        return _npm_specs_compatible(f"=={version}", spec)


def _suggest_resolution(
    pkg: str,
    spec_a: str, spec_b: str,
    src_a: str, src_b: str,
    ecosystem: str,
) -> str:
    pm = "pip install" if ecosystem == "python" else "npm install"
    return (
        f"`{src_a}` requires `{pkg}{spec_a}` but `{src_b}` requires `{pkg}{spec_b}`. "
        f"These cannot be satisfied simultaneously. Options:\n"
        f"  1. Downgrade `{src_a}` or `{src_b}` to versions that agree on `{pkg}`\n"
        f"  2. Check if `{src_a}` or `{src_b}` have a newer release that relaxed this constraint\n"
        f"  3. Search for the conflict: `{pkg}` changelog between the two required ranges"
    )
