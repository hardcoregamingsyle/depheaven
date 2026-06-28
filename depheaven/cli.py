"""DepHeaven CLI — heaven {path} dephell"""

import sys
from pathlib import Path

import click

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.panel import Panel
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None

from . import __version__
from .analyzer import analyze_file, analyze_directory
from .fixer import build_updates, apply_fixes
from .languages.base import AnalysisResult


def _echo(msg):
    if HAS_RICH:
        console.print(msg)
    else:
        import re
        click.echo(re.sub(r"\[/?[a-z/ _]+\]", "", msg))


def _render_result(result: AnalysisResult) -> bool:
    """Print one file's dep table. Returns True if any issues found."""
    deps = result.dependencies
    if not deps and not result.errors:
        return False

    rel = result.file_path
    try:
        rel = result.file_path.relative_to(Path.cwd())
    except ValueError:
        pass

    _echo(f"\n[bold]{rel}[/bold] [dim]({result.language})[/dim]")

    if result.manifest_path:
        mrel = result.manifest_path
        try:
            mrel = result.manifest_path.relative_to(Path.cwd())
        except ValueError:
            pass
        _echo(f"  [dim]manifest: {mrel}[/dim]")
    else:
        _echo("  [yellow]⚠  no manifest found[/yellow]")

    for err in result.errors:
        _echo(f"  [red]error:[/red] {err}")

    if HAS_RICH:
        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
        tbl.add_column("", width=2)
        tbl.add_column("Package", style="bold")
        tbl.add_column("Installed", style="dim")
        tbl.add_column("Latest")
        tbl.add_column("Status")
        icons = {
            "ok":       "[green]✓[/green]",
            "outdated": "[cyan]→[/cyan]",
            "breaking": "[yellow]⚠[/yellow]",
            "missing":  "[red]✗[/red]",
        }
        styles = {"ok": "green", "outdated": "cyan", "breaking": "yellow", "missing": "red"}
        for dep in deps:
            s = dep.status
            notes = f" [dim]{dep.breaking_change_notes}[/dim]" if dep.breaking_change_notes else ""
            tbl.add_row(
                icons.get(s, "?"),
                dep.name,
                dep.current_version or "—",
                dep.latest_version or "[dim]unknown[/dim]",
                f"[{styles.get(s,'white')}]{s}[/{styles.get(s,'white')}]{notes}",
            )
        console.print(tbl)
    else:
        for dep in deps:
            click.echo(
                f"  [{dep.status.upper()}] {dep.name}  "
                f"{dep.current_version or '—'} → {dep.latest_version or '?'}"
            )

    return any(d.status != "ok" for d in deps)


def _render_insights(result: AnalysisResult) -> None:
    """Print changelog insights, usage hits, and codemod results."""
    if not result.insights:
        return

    for insight in result.insights:
        if not (insight.changelog_summary or insight.api_changes or insight.codemod_changes):
            continue

        header = (
            f"\n  [bold]{insight.package}[/bold] "
            f"[dim]{insight.from_version}[/dim] [cyan]→[/cyan] [bold]{insight.to_version}[/bold]"
        )
        _echo(header)

        # Structured changelog output (parsed, not LLM-generated)
        if insight.changelog_summary:
            for line in insight.changelog_summary.splitlines():
                if line.strip():
                    _echo(f"    [dim]{line}[/dim]")

        # Codemods: auto-fixed + usage warnings (these are the most useful lines)
        if insight.codemod_changes:
            auto = [c for c in insight.codemod_changes if c.startswith("[line") and "Auto-fixed" in c]
            manual = [c for c in insight.codemod_changes if "Manual review" in c or "You use" in c]

            if auto:
                _echo(f"    [green]Auto-fixed ({len(auto)}):[/green]")
                for c in auto[:8]:
                    _echo(f"      ✓ {c}")
            if manual:
                _echo(f"    [yellow]Needs attention ({len(manual)}):[/yellow]")
                for c in manual[:8]:
                    _echo(f"      ⚠ {c}")

        # Migration hints from the real changelog
        if insight.migration_steps:
            _echo("    [cyan]Migration notes (from changelog):[/cyan]")
            for h in insight.migration_steps[:3]:
                _echo(f"      → {h[:120]}")


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="heaven")
@click.pass_context
def main(ctx):
    """DepHeaven — escape dependency hell.

    \b
    Usage:
      heaven {file}       dephell
      heaven {directory}  dephell
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command("dephell")
@click.argument("target", type=click.Path(exists=True))
@click.option("--yes", "-y", is_flag=True, help="Apply fixes without prompting.")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would change, don't write.")
@click.option("--verbose", "-v", is_flag=True, help="Show all deps, not just problems.")
@click.option("--offline", is_flag=True, help="Skip all network requests.")
def dephell(target, yes, dry_run, verbose, offline):
    """Analyze TARGET (file or directory) and fix its dependencies.

    \b
    What it does:
      • Detects imports and cross-references your manifest
      • Fetches latest versions from PyPI / npm registry
      • Fetches real changelogs from GitHub Releases (no auth needed)
      • Parses changelogs to find removed/renamed/deprecated APIs
      • Scans your code to find which affected APIs you actually call
      • Auto-rewrites known breaking patterns (pydantic, numpy, lodash, etc.)
      • Flags what needs manual attention with line numbers
    """
    path = Path(target).resolve()

    if HAS_RICH:
        offline_tag = " [dim](offline)[/dim]" if offline else ""
        console.print(Panel.fit(
            f"[bold magenta]DepHeaven[/bold magenta] [dim]v{__version__}[/dim]{offline_tag}  "
            f"[cyan]analyzing[/cyan] [bold]{path}[/bold]",
            border_style="magenta",
        ))
    else:
        click.echo(f"\nDepHeaven v{__version__} — analyzing {path}\n")

    if path.is_file():
        r = analyze_file(path, offline=offline)
        if r is None:
            _echo(f"[red]Unsupported file type:[/red] {path.suffix}")
            sys.exit(1)
        results = [r]
    else:
        results = analyze_directory(path, offline=offline)
        if not results:
            _echo("[yellow]No supported source files found.[/yellow]")
            sys.exit(0)

    any_issues = False
    for result in results:
        if verbose or any(d.status != "ok" for d in result.dependencies):
            any_issues = _render_result(result) or any_issues
            _render_insights(result)

    if not any_issues:
        _echo("\n[green]✓ Everything looks good — no issues found![/green]")
        sys.exit(0)

    if dry_run:
        _echo("\n[dim](dry run — no changes written)[/dim]")
        sys.exit(0)

    fixable = [(r, build_updates(r)) for r in results]
    fixable = [(r, u) for r, u in fixable if u and r.manifest_path]

    if not fixable:
        _echo("\n[yellow]No automatic manifest fixes available.[/yellow]")
        sys.exit(1)

    _echo(f"\n[bold]Manifest fixes available for {len(fixable)} file(s).[/bold]")
    if not yes:
        click.confirm("Apply fixes?", default=True, abort=True)

    wrote = 0
    for result, updates in fixable:
        try:
            apply_fixes(result)
            wrote += 1
            mrel = result.manifest_path
            try:
                mrel = result.manifest_path.relative_to(Path.cwd())
            except ValueError:
                pass
            _echo(f"  [green]✓ updated {mrel}[/green]")
        except RuntimeError as e:
            _echo(f"  [red]✗ {e}[/red]")

    _echo(
        f"\n[bold green]Done![/bold green] Updated {wrote} manifest(s). "
        "Run your package manager to install the updates."
    )


@main.command("graphcheck")
@click.argument("target", type=click.Path(exists=True))
@click.option("--depth", "-d", default=4, show_default=True,
              help="How many levels deep to trace transitive dependencies.")
@click.option("--verbose", "-v", is_flag=True, help="Show full dependency tree, not just conflicts.")
def graphcheck(target, depth, verbose):
    """Detect deep transitive dependency conflicts in TARGET.

    \b
    Finds conflicts like:
      Your code needs A
      A needs C>=2.0
      C needs B>=3.0
      But A also requires B<2.0  ← conflict buried 3 levels deep

    Fetches full transitive dependency trees from PyPI / npm.
    """
    from .graph import build_graph, detect_conflicts
    from .languages.python import PythonAnalyzer
    from .languages.javascript import JavaScriptAnalyzer

    path = Path(target).resolve()

    if HAS_RICH:
        console.print(Panel.fit(
            f"[bold magenta]DepHeaven[/bold magenta] [bold cyan]graphcheck[/bold cyan]  "
            f"[dim]depth={depth}[/dim]  [bold]{path}[/bold]",
            border_style="cyan",
        ))
    else:
        click.echo(f"\nDepHeaven graphcheck — {path} (depth {depth})\n")

    # Find the manifest for this path
    manifest_path = None
    ecosystem = "python"

    py = PythonAnalyzer()
    js = JavaScriptAnalyzer()

    check_path = path if path.is_dir() else path.parent
    # Search only within the target directory (don't walk above it)
    for name in ("requirements.txt", "pyproject.toml", "Pipfile", "package.json"):
        c = check_path / name
        if c.exists():
            manifest_path = c
            break

    if not manifest_path:
        _echo("[red]No manifest found (requirements.txt / package.json).[/red]")
        raise SystemExit(1)

    _echo(f"  [dim]manifest:[/dim] [bold]{manifest_path}[/bold]")

    # Parse manifest to get root packages
    if manifest_path.name == "package.json":
        ecosystem = "npm"
        root_packages = _parse_package_json_deps(manifest_path)
    else:
        ecosystem = "python"
        root_packages = py.parse_manifest(manifest_path)

    if not root_packages:
        _echo("[yellow]No dependencies found in manifest.[/yellow]")
        raise SystemExit(0)

    _echo(f"  [dim]{len(root_packages)} root packages, tracing {depth} levels deep...[/dim]\n")

    # Build graph
    graph = build_graph(root_packages, ecosystem=ecosystem, max_depth=depth)

    total_nodes = len(graph.nodes)
    total_edges = len(graph.edges)
    _echo(f"  [dim]Graph built: {total_nodes} packages, {total_edges} edges[/dim]\n")

    # Optional: print tree
    if verbose:
        _render_tree(graph, root_packages)

    # Detect conflicts
    conflicts = detect_conflicts(graph, ecosystem=ecosystem)

    if not conflicts:
        _echo("[bold green]✓ No transitive dependency conflicts detected.[/bold green]")
        raise SystemExit(0)

    _echo(f"[bold red]✗ {len(conflicts)} conflict(s) detected:[/bold red]\n")

    for i, conflict in enumerate(conflicts, 1):
        _render_conflict(i, conflict)

    raise SystemExit(1)


def _parse_package_json_deps(manifest_path: Path) -> dict[str, str]:
    import json
    try:
        data = json.loads(manifest_path.read_text())
        deps = {}
        for section in ("dependencies", "devDependencies"):
            for name, ver in (data.get(section) or {}).items():
                deps[name] = ver.lstrip("^~>=<! ")
        return deps
    except Exception:
        return {}


def _render_tree(graph, root_packages: dict[str, str]) -> None:
    """Print a simple indented dependency tree."""
    _echo("[bold]Dependency tree:[/bold]")
    from collections import defaultdict
    adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for src, dst, spec in graph.edges:
        adj[src].append((dst, spec))

    visited: set[str] = set()

    def _print_node(name: str, spec: str, indent: int) -> None:
        key = name.lower().replace("-", "_")
        node = graph.get_node(key)
        ver = node.version if node else "?"
        prefix = "  " * indent
        marker = "[dim](already shown)[/dim]" if key in visited else ""
        color = "yellow" if node and node.fetch_error else "white"
        _echo(f"{prefix}[{color}]{name}[/{color}] [dim]{ver}[/dim] [dim]{spec}[/dim] {marker}")
        if key in visited:
            return
        visited.add(key)
        for dep_name, dep_spec in adj.get(key, [])[:10]:  # limit width
            _print_node(dep_name, dep_spec, indent + 1)

    for pkg in root_packages:
        _print_node(pkg, "", 0)
    _echo("")


def _render_conflict(i: int, conflict) -> None:
    """Render a single ConflictChain."""
    pkg = conflict.package
    reqs = conflict.requirements

    if HAS_RICH:
        if conflict.conflict_type == "manifest_pin_conflict":
            title = f"[bold red]Conflict {i}:[/bold red] [bold]{pkg}[/bold] — your pin vs transitive requirement"
        else:
            title = f"[bold red]Conflict {i}:[/bold red] [bold]{pkg}[/bold] — incompatible version requirements"
        console.print(title)

        # Show each requirement chain
        for requirer, spec, resolved in reqs:
            _echo(f"  [yellow]•[/yellow] [bold]{requirer}[/bold] requires [cyan]{pkg}{spec}[/cyan]")

        # Show the conflict chains (how we got there)
        if conflict.path_a and len(conflict.path_a) > 1:
            chain_a = " → ".join(conflict.path_a)
            _echo(f"    [dim]path: {chain_a}[/dim]")
        if conflict.path_b and len(conflict.path_b) > 1:
            chain_b = " → ".join(conflict.path_b)
            _echo(f"    [dim]path: {chain_b}[/dim]")

        # Resolution
        if conflict.suggestion:
            _echo(f"\n  [green]Resolution:[/green]")
            for line in conflict.suggestion.splitlines():
                _echo(f"  {line}")
        _echo("")
    else:
        click.echo(f"\nConflict {i}: {pkg}")
        for requirer, spec, resolved in reqs:
            click.echo(f"  {requirer} requires {pkg}{spec}")
        if conflict.suggestion:
            click.echo(f"  Fix: {conflict.suggestion}")


# Support: heaven {path} dephell  OR  heaven {path} graphcheck  (path before subcommand)
class _ReorderGroup(click.Group):
    def parse_args(self, ctx, args):
        subcmds = {"dephell", "graphcheck"}
        if len(args) >= 2 and args[1] in subcmds and not args[0].startswith("-"):
            args = [args[1], args[0]] + list(args[2:])
        return super().parse_args(ctx, args)


main.__class__ = _ReorderGroup


if __name__ == "__main__":
    main()
