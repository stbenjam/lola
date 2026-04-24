"""
Setup CLI command for checking and configuring module dependencies.
"""

from __future__ import annotations

from typing import Optional

import click
from rich.console import Console

from lola.cli.completions import complete_module_names
from lola.cli.mod import list_registered_modules, load_registered_module
from lola.cli.utils import handle_lola_error
from lola.config import MODULES_DIR
from lola.exceptions import ModuleNotFoundError
from lola.models import Module
from lola.prompts import is_interactive, select_module
from lola.setup import check_all, check_dependency, run_setup
from lola.utils import ensure_lola_dirs

console = Console()


def _print_dep_status(
    results: list[tuple], module_name: str, verbose: bool = False
) -> tuple[int, int]:
    """Print dependency status and return (satisfied, total) counts."""
    satisfied = 0
    total = len(results)
    for dep, ok, msg in results:
        if ok:
            satisfied += 1
            console.print(f"  [green]ok[/green]  {dep.name} — {dep.description}")
        else:
            console.print(f"  [red]missing[/red]  {dep.name} — {dep.description}")
            if verbose and msg:
                console.print(f"    [dim]{msg}[/dim]")
    return satisfied, total


def _run_interactive_setup(module: Module) -> None:
    """Walk through unsatisfied deps and offer to run install scripts."""
    results = check_all(module)
    satisfied, total = _print_dep_status(results, module.name)

    if satisfied == total:
        console.print()
        console.print(f"[green]All {total} dependencies satisfied.[/green]")
        return

    console.print()
    for dep, ok, _msg in results:
        if ok:
            continue
        if not dep.install:
            console.print(
                f"  [yellow]{dep.name}[/yellow]: no install script — configure manually"
            )
            continue

        if not click.confirm(f"Set up {dep.name}? ({dep.description})", default=True):
            console.print(f"  [dim]Skipped {dep.name}[/dim]")
            continue

        console.print(f"  [dim]Running setup for {dep.name}...[/dim]")
        success = run_setup(dep, module)
        if success:
            ok_now, _ = check_dependency(dep, module)
            if ok_now:
                console.print(f"  [green]ok[/green]  {dep.name} — now configured")
                satisfied += 1
            else:
                console.print(
                    f"  [yellow]warning[/yellow]  {dep.name} — script finished but check still fails"
                )
        else:
            console.print(f"  [red]error[/red]  {dep.name} — setup script failed")

    console.print()
    console.print(f"{satisfied} of {total} dependencies satisfied.")


@click.command(name="setup")
@click.argument(
    "module_name",
    required=False,
    default=None,
    shell_complete=complete_module_names,
)
@click.option("--check", is_flag=True, help="Only check status, don't run setup")
@click.option("--dep", "dep_name", default=None, help="Check/setup a specific dependency only")
@click.option("--all", "all_modules", is_flag=True, hidden=True, help="Check all registered modules (default when no module specified)")
@click.option("-v", "--verbose", is_flag=True, help="Show detailed output")
def setup_cmd(
    module_name: Optional[str],
    check: bool,
    dep_name: Optional[str],
    all_modules: bool,
    verbose: bool,
):
    """
    Check and configure module dependencies.

    Walks through a module's setup requirements, checking which
    dependencies are satisfied and offering to run setup scripts
    for those that aren't.

    \b
    Examples:
        lola setup                       # Check/setup all modules
        lola setup --check               # Just show status for all modules
        lola setup ci                    # Setup for a specific module
        lola setup ci --check            # Just show status for one module
        lola setup ci --dep gh           # Set up only the gh dependency
    """
    ensure_lola_dirs()

    if all_modules or module_name is None:
        _setup_all(check, verbose)
        return

    module_path = MODULES_DIR / module_name
    if not module_path.exists():
        handle_lola_error(ModuleNotFoundError(module_name))
    module = load_registered_module(module_path)
    if not module:
        console.print(f"[red]Could not load module '{module_name}'[/red]")
        raise SystemExit(1)

    if not module.setup:
        console.print(f"[dim]Module '{module_name}' has no setup dependencies.[/dim]")
        return

    if dep_name:
        _setup_single_dep(module, dep_name, check, verbose)
        return

    console.print(f"[bold]Setup status for {module_name}:[/bold]")
    console.print()

    if check:
        results = check_all(module)
        satisfied, total = _print_dep_status(results, module.name, verbose)
        console.print()
        console.print(f"{satisfied} of {total} dependencies satisfied.")
        if satisfied < total:
            raise SystemExit(1)
    else:
        _run_interactive_setup(module)


def _setup_single_dep(
    module: Module, dep_name: str, check_only: bool, verbose: bool
) -> None:
    """Check/setup a specific dependency by name."""
    dep = next((d for d in module.setup if d.name == dep_name), None)
    if not dep:
        console.print(f"[red]No setup dependency named '{dep_name}' in {module.name}[/red]")
        available = ", ".join(d.name for d in module.setup)
        console.print(f"[dim]Available: {available}[/dim]")
        raise SystemExit(1)

    ok, msg = check_dependency(dep, module)
    if ok:
        console.print(f"[green]ok[/green]  {dep.name} — {dep.description}")
        return

    console.print(f"[red]missing[/red]  {dep.name} — {dep.description}")
    if verbose and msg:
        console.print(f"  [dim]{msg}[/dim]")

    if check_only:
        raise SystemExit(1)

    if not dep.install:
        console.print("[yellow]No install script — configure manually[/yellow]")
        raise SystemExit(1)

    if not is_interactive() or click.confirm(
        f"Set up {dep.name}?", default=True
    ):
        console.print(f"[dim]Running setup for {dep.name}...[/dim]")
        success = run_setup(dep, module)
        if success:
            ok_now, _ = check_dependency(dep, module)
            if ok_now:
                console.print(f"[green]ok[/green]  {dep.name} — now configured")
            else:
                console.print(
                    f"[yellow]warning[/yellow]  {dep.name} — script finished but check still fails"
                )
                raise SystemExit(1)
        else:
            console.print(f"[red]error[/red]  {dep.name} — setup script failed")
            raise SystemExit(1)


def _setup_all(check_only: bool, verbose: bool) -> None:
    """Check/setup all registered modules that have setup dependencies."""
    registered = list_registered_modules()
    with_setup = [m for m in registered if m.setup]

    if not with_setup:
        console.print("[dim]No modules with setup dependencies found.[/dim]")
        return

    all_satisfied = 0
    all_total = 0
    modules_complete = 0

    for module in with_setup:
        console.print(f"[bold]{module.name}:[/bold]")
        results = check_all(module)
        satisfied, total = _print_dep_status(results, module.name, verbose)
        all_satisfied += satisfied
        all_total += total
        if satisfied == total:
            modules_complete += 1
        console.print()

    if not check_only and all_satisfied < all_total:
        for module in with_setup:
            results = check_all(module)
            unmet = [(dep, msg) for dep, ok, msg in results if not ok and dep.install]
            if not unmet:
                continue
            console.print(f"[bold]Setting up {module.name}:[/bold]")
            for dep, _msg in unmet:
                if not click.confirm(
                    f"  Set up {dep.name}? ({dep.description})", default=True
                ):
                    continue
                console.print(f"  [dim]Running setup for {dep.name}...[/dim]")
                success = run_setup(dep, module)
                if success:
                    ok_now, _ = check_dependency(dep, module)
                    if ok_now:
                        console.print(f"  [green]ok[/green]  {dep.name} — now configured")
                        all_satisfied += 1
                    else:
                        console.print(
                            f"  [yellow]warning[/yellow]  {dep.name} — check still fails"
                        )
                else:
                    console.print(f"  [red]error[/red]  {dep.name} — setup failed")
            console.print()

    console.print(
        f"{modules_complete} of {len(with_setup)} modules fully configured, "
        f"{all_satisfied} of {all_total} total dependencies satisfied."
    )
    if check_only and all_satisfied < all_total:
        raise SystemExit(1)
