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
    return (
        f"`{src_a}` requires `{pkg}{spec_a}` but `{src_b}` requires `{pkg}{spec_b}`. "
        f"These cannot be satisfied simultaneously. Options:\n"
        f"  1. Downgrade `{src_a}` or `{src_b}` to versions that agree on `{pkg}`\n"
        f"  2. Check if `{src_a}` or `{src_b}` have a newer release that relaxed this constraint\n"
        f"  3. Search for the conflict: `{pkg}` changelog between the two required ranges"
    )


# ── Resolution engine ─────────────────────────────────────────────────────────

@dataclass
class Resolution:
    """The outcome of attempting to resolve a conflict."""
    conflict: ConflictChain
    status: str          # "resolved" | "upgrade_required" | "unresolvable"
    # For "resolved": a version of the conflicting package that satisfies everyone
    resolved_version: Optional[str] = None
    # For "upgrade_required": which requirer to upgrade and to what version
    upgrade_package: Optional[str] = None
    upgrade_to: Optional[str] = None
    upgrade_relaxes_constraint: Optional[str] = None  # the new (relaxed) specifier
    # For "unresolvable": why
    reason: Optional[str] = None
    # Manifest changes to write
    manifest_changes: dict[str, str] = field(default_factory=dict)  # {pkg: version}


def resolve_all(
    conflicts: list[ConflictChain],
    graph: DependencyGraph,
    ecosystem: str = "python",
) -> list[Resolution]:
    """
    Attempt to resolve every conflict.
    Returns a Resolution for each conflict.
    """
    resolutions: list[Resolution] = []

    # Collect ALL constraints from transitive edges only.
    # We deliberately exclude the manifest pins — those are what we're trying to fix.
    all_constraints: dict[str, list[str]] = defaultdict(list)
    root_keys = {k.lower().replace("-", "_") for k in graph.root_packages}
    for src, dst, spec in graph.edges:
        if spec:
            # Skip edges that originate from a root package pinning itself
            src_key = src.lower().replace("-", "_")
            dst_key = dst.lower().replace("-", "_")
            if src_key in root_keys and dst_key in root_keys:
                continue  # manifest-to-manifest: skip, we'll rewrite both
            all_constraints[dst].append(spec)

    for conflict in conflicts:
        res = _resolve_one(conflict, all_constraints, graph, ecosystem)
        resolutions.append(res)

    return resolutions


def _resolve_one(
    conflict: ConflictChain,
    all_constraints: dict[str, list[str]],
    graph: DependencyGraph,
    ecosystem: str,
) -> Resolution:
    pkg = conflict.package

    # For manifest_pin_conflict: the manifest pin IS the problem — exclude it
    # and find a version satisfying only the transitive requirements
    if conflict.conflict_type == "manifest_pin_conflict":
        transitive_specs = [
            spec for (requirer, spec, _) in conflict.requirements
            if requirer != "your manifest"
        ]
        # Add any other transitive constraints on this package
        extra = [s for s in all_constraints.get(pkg, [])
                 if "extra ==" not in s]  # skip optional extras
        constraints = list(set(transitive_specs + extra))
    else:
        constraints = list(set(all_constraints.get(pkg, [])))

    # Filter out marker-only constraints (no version info)
    constraints = [c for c in constraints if c and any(ch.isdigit() for ch in c)]

    # 1. Try to find a version of `pkg` that satisfies all constraints at once
    compatible = _find_compatible_version(pkg, constraints, ecosystem)

    if compatible:
        return Resolution(
            conflict=conflict,
            status="resolved",
            resolved_version=compatible,
            manifest_changes={pkg: compatible},
        )

    # 2. No single version works — find which requirer to upgrade
    # Strategy: for each requirer that is a direct dependency (in root_packages),
    # check if its newer versions have a more relaxed constraint on `pkg`
    for requirer, spec, _ in conflict.requirements:
        if requirer == "your manifest":
            continue
        norm_req = requirer.lower().replace("-", "_")
        if norm_req not in {k.lower().replace("-", "_") for k in graph.root_packages}:
            continue  # not a direct dep — can't easily upgrade it

        upgrade_ver, new_spec = _find_upgrader(requirer, pkg, spec, ecosystem)
        if upgrade_ver and new_spec:
            # Re-check: does upgrading this requirer resolve the conflict?
            test_constraints = [c for c in constraints if c != spec] + [new_spec]
            retry = _find_compatible_version(pkg, test_constraints, ecosystem)
            if retry:
                return Resolution(
                    conflict=conflict,
                    status="upgrade_required",
                    upgrade_package=requirer,
                    upgrade_to=upgrade_ver,
                    upgrade_relaxes_constraint=new_spec,
                    resolved_version=retry,
                    manifest_changes={requirer: upgrade_ver, pkg: retry},
                )

    return Resolution(
        conflict=conflict,
        status="unresolvable",
        reason=(
            f"No version of `{pkg}` satisfies all constraints simultaneously, "
            f"and no upgrade of a direct dependency was found that relaxes them. "
            f"You may need to fork a dependency or vendor it."
        ),
    )


def _find_compatible_version(
    package: str,
    constraints: list[str],
    ecosystem: str,
) -> Optional[str]:
    """
    Fetch all released versions of `package` and return the highest one
    that satisfies every constraint in `constraints`.
    """
    versions = _fetch_all_versions(package, ecosystem)
    if not versions:
        return None

    if ecosystem == "python":
        return _best_python_version(versions, constraints)
    else:
        return _best_npm_version(versions, constraints)


def _best_python_version(versions: list[str], constraints: list[str]) -> Optional[str]:
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        combined = SpecifierSet(",".join(constraints), prereleases=False)
        candidates = []
        for v in versions:
            try:
                vv = Version(v)
                if not vv.is_prerelease and vv in combined:
                    candidates.append(vv)
            except Exception:
                continue
        return str(max(candidates)) if candidates else None
    except Exception:
        return None


def _best_npm_version(versions: list[str], constraints: list[str]) -> Optional[str]:
    """Find highest npm version satisfying all constraints."""
    # Parse constraints to get required major versions
    required_majors: set[int] = set()
    min_ver = (0, 0, 0)

    for spec in constraints:
        spec = spec.strip()
        # caret: ^X.Y.Z → major must equal X
        m = re.match(r"^\^(\d+)", spec)
        if m:
            required_majors.add(int(m.group(1)))
            continue
        # tilde: ~X.Y → major must equal X
        m = re.match(r"^~(\d+)", spec)
        if m:
            required_majors.add(int(m.group(1)))
            continue
        # exact: X.Y.Z or ==X.Y.Z
        m = re.match(r"^=?=?(\d+)\.(\d+)\.?(\d*)", spec.lstrip("v"))
        if m:
            required_majors.add(int(m.group(1)))

    if len(required_majors) > 1:
        return None  # conflicting majors — truly incompatible

    def parse_semver(v: str) -> tuple[int, int, int]:
        parts = re.sub(r"[^0-9.]", "", v).split(".")
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0,
                    int(parts[2]) if len(parts) > 2 else 0)
        except (ValueError, IndexError):
            return (0, 0, 0)

    target_major = next(iter(required_majors)) if required_majors else None
    candidates = []
    for v in versions:
        parsed = parse_semver(v)
        if "-" in v:  # skip pre-releases
            continue
        if target_major is not None and parsed[0] != target_major:
            continue
        candidates.append((parsed, v))

    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def _fetch_all_versions(package: str, ecosystem: str) -> list[str]:
    """Fetch all released versions of a package."""
    if ecosystem == "python":
        data = _get_json(f"https://pypi.org/pypi/{package}/json")
        if not data:
            return []
        return list((data.get("releases") or {}).keys())
    else:
        encoded = package.replace("/", "%2F")
        data = _get_json(f"https://registry.npmjs.org/{encoded}")
        if not data:
            return []
        return list((data.get("versions") or {}).keys())


def _find_upgrader(
    requirer: str,
    conflicting_pkg: str,
    current_spec: str,
    ecosystem: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Check if a newer version of `requirer` has a more relaxed constraint on
    `conflicting_pkg`. Returns (upgrade_version, new_specifier) or (None, None).
    """
    versions = _fetch_all_versions(requirer, ecosystem)
    if not versions:
        return None, None

    # Sort versions descending and check each newer release
    def ver_key(v: str) -> tuple:
        parts = re.sub(r"[^0-9.]", "", v).split(".")
        try:
            return tuple(int(x) for x in parts[:3])
        except ValueError:
            return (0,)

    try:
        sorted_vers = sorted(
            [v for v in versions if "-" not in v],
            key=ver_key,
            reverse=True,
        )
    except Exception:
        return None, None

    for ver in sorted_vers[:15]:  # check top 15 newer releases
        if ecosystem == "python":
            meta = fetch_pypi_metadata(requirer, ver)
        else:
            meta = fetch_npm_metadata(requirer, ver)

        if not meta:
            continue

        for req in meta.requires:
            norm = req.name.lower().replace("-", "_")
            if norm == conflicting_pkg.lower().replace("-", "_"):
                if req.specifier != current_spec:
                    return ver, req.specifier

    return None, None


# ── Apply resolutions to manifest files ───────────────────────────────────────

def apply_resolutions(
    resolutions: list[Resolution],
    manifest_path,
    ecosystem: str,
) -> dict[str, list[str]]:
    """
    Write resolved versions back to the manifest file.
    Returns {"updated": [...], "skipped": [...], "unresolvable": [...]}.
    """
    import re as _re
    from pathlib import Path

    manifest_path = Path(manifest_path)
    outcome = {"updated": [], "skipped": [], "unresolvable": []}

    # Collect all changes: {pkg_name: version}
    all_changes: dict[str, str] = {}
    for res in resolutions:
        if res.status in ("resolved", "upgrade_required"):
            all_changes.update(res.manifest_changes)
        else:
            outcome["unresolvable"].append(res.conflict.package)

    if not all_changes:
        return outcome

    if ecosystem == "python":
        _apply_python_manifest(manifest_path, all_changes, outcome)
    else:
        _apply_npm_manifest(manifest_path, all_changes, outcome)

    return outcome


def _apply_python_manifest(manifest_path, changes: dict[str, str], outcome: dict) -> None:
    import re
    text = manifest_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    new_lines = []
    updated: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        m = re.match(r"^([\w\-\.]+)", stripped)
        if m:
            key = m.group(1).lower().replace("-", "_")
            if key in {k.lower().replace("-", "_") for k in changes}:
                # Find matching key
                match_key = next(k for k in changes
                                 if k.lower().replace("-", "_") == key)
                new_ver = changes[match_key]
                extras_m = re.match(r"^([\w\-\.]+)(\[[^\]]+\])?", stripped)
                pkg_name = m.group(1)
                extras = extras_m.group(2) or "" if extras_m else ""
                new_lines.append(f"{pkg_name}{extras}=={new_ver}\n")
                outcome["updated"].append(f"{pkg_name}=={new_ver}")
                updated.add(key)
                continue
        new_lines.append(line)

    # Add packages not already in manifest
    existing = {re.match(r"^([\w\-\.]+)", l.strip()).group(1).lower().replace("-", "_")
                for l in lines if l.strip() and not l.strip().startswith("#")
                and re.match(r"^([\w\-\.]+)", l.strip())}
    for pkg, ver in changes.items():
        key = pkg.lower().replace("-", "_")
        if key not in existing and key not in updated:
            new_lines.append(f"{pkg}=={ver}\n")
            outcome["updated"].append(f"{pkg}=={ver} (added)")

    manifest_path.write_text("".join(new_lines), encoding="utf-8")


def _apply_npm_manifest(manifest_path, changes: dict[str, str], outcome: dict) -> None:
    import json
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    for section in ("dependencies", "devDependencies"):
        if section not in data:
            continue
        for pkg in list(data[section].keys()):
            key = pkg.lower()
            match = next((k for k in changes if k.lower() == key), None)
            if match:
                data[section][pkg] = changes[match]
                outcome["updated"].append(f"{pkg}=={changes[match]}")

    manifest_path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
