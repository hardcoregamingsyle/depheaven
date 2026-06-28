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
        # strip rich markup for plain output
        import re
        click.echo(re.sub(r"\[/?[a-z/ _]+\]", "", msg))


def _render_result(result: AnalysisResult) -> bool:
    """Print one file's results. Returns True if any issues found."""
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
        icons = {"ok": "[green]✓[/green]", "outdated": "[cyan]→[/cyan]",
                 "breaking": "[yellow]⚠[/yellow]", "missing": "[red]✗[/red]"}
        styles = {"ok": "green", "outdated": "cyan", "breaking": "yellow", "missing": "red"}
        for dep in deps:
            s = dep.status
            tbl.add_row(
                icons.get(s, "?"),
                dep.name,
                dep.current_version or "—",
                dep.latest_version or "[dim]unknown[/dim]",
                f"[{styles.get(s,'white')}]{s}[/{styles.get(s,'white')}]",
            )
        console.print(tbl)
    else:
        for dep in deps:
            click.echo(f"  [{dep.status.upper()}] {dep.name}  "
                       f"{dep.current_version or '—'} → {dep.latest_version or '?'}")

    return any(d.status != "ok" for d in deps)


def _render_insights(result: AnalysisResult) -> None:
    """Print changelog insights and codemod results for a file's deps."""
    if not result.insights:
        return

    for insight in result.insights:
        header = (f"[bold]{insight.package}[/bold] "
                  f"[dim]{insight.from_version}[/dim] → [cyan]{insight.to_version}[/cyan]")
        if insight.ollama_used:
            header += " [dim](Ollama)[/dim]"

        _echo(f"\n  {header}")

        if insight.changelog_summary:
            _echo(f"    [dim]Summary:[/dim] {insight.changelog_summary}")

        if insight.api_changes:
            _echo("    [yellow]API changes:[/yellow]")
            for change in insight.api_changes[:6]:
                _echo(f"      • {change}")

        if insight.codemod_changes:
            _echo("    [green]Auto-fixed:[/green]")
            for change in insight.codemod_changes:
                _echo(f"      ✓ {change}")

        if insight.migration_steps:
            _echo("    [cyan]Migration steps:[/cyan]")
            for i, step in enumerate(insight.migration_steps[:5], 1):
                _echo(f"      {i}. {step}")


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
@click.option("--dry-run", "-n", is_flag=True, help="Show what would change but don't write.")
@click.option("--verbose", "-v", is_flag=True, help="Show all deps, not just problems.")
@click.option("--deep", is_flag=True,
              help="Fetch real changelogs + use local Ollama LLM to understand and rewrite code. "
                   "Requires `ollama serve` running. No API key needed.")
@click.option("--offline", is_flag=True, help="Skip all network requests.")
def dephell(target, yes, dry_run, verbose, deep, offline):
    """Analyze TARGET (file or directory) and fix its dependencies.

    \b
    Modes:
      default   Registry version checks + known breaking-change database
      --deep    + real changelog fetching + local Ollama LLM (no API key)
      --offline Static analysis only (no network)
    """
    path = Path(target).resolve()

    if HAS_RICH:
        mode_tag = "[bold yellow] --deep[/bold yellow]" if deep else ""
        console.print(Panel.fit(
            f"[bold magenta]DepHeaven[/bold magenta] [dim]v{__version__}[/dim]{mode_tag}  "
            f"[cyan]analyzing[/cyan] [bold]{path}[/bold]",
            border_style="magenta",
        ))
    else:
        click.echo(f"\nDepHeaven v{__version__} — analyzing {path}\n")

    if deep and not offline:
        try:
            from . import ollama_client
            if ollama_client.is_available():
                model = ollama_client.best_model()
                _echo(f"  [green]Ollama detected[/green] — using model [bold]{model}[/bold]")
            else:
                _echo("  [yellow]--deep requested but Ollama is not running.[/yellow] "
                      "Start it with: [bold]ollama serve[/bold]  "
                      "Continuing with changelog-only analysis.")
        except Exception:
            pass

    if path.is_file():
        r = analyze_file(path, offline=offline, deep=deep)
        if r is None:
            _echo(f"[red]Unsupported file type:[/red] {path.suffix}")
            sys.exit(1)
        results = [r]
    else:
        results = analyze_directory(path, offline=offline, deep=deep)
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
        _echo("\n[yellow]No automatic fixes available (manifests missing or no newer versions found).[/yellow]")
        sys.exit(1)

    _echo(f"\n[bold]Fixes available for {len(fixable)} manifest(s).[/bold]")
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

    _echo(f"\n[bold green]Done![/bold green] Updated {wrote} manifest(s). "
          "Run your package manager to install the updates.")


# Support: heaven {path} dephell  (path before subcommand)
class _ReorderGroup(click.Group):
    def parse_args(self, ctx, args):
        if len(args) >= 2 and args[1] == "dephell" and not args[0].startswith("-"):
            args = ["dephell", args[0]] + list(args[2:])
        return super().parse_args(ctx, args)


# Patch main to use reordering group
main.__class__ = _ReorderGroup


if __name__ == "__main__":
    main()
