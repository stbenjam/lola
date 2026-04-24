"""
Bundle management CLI commands.

Commands for installing, listing, and inspecting bundles from marketplaces.
"""

from pathlib import Path
from typing import Optional

import click
from rich.console import Console

from lola.cli.completions import complete_bundle_names
from lola.config import MODULES_DIR, MARKET_DIR, CACHE_DIR
from lola.market.manager import MarketplaceRegistry
from lola.models import Marketplace
from lola.prompts import is_interactive, select_assistants
from lola.targets import TARGETS, get_registry
from lola.targets.install import install_to_assistant
from lola.utils import ensure_lola_dirs, get_local_modules_path, resolve_marketplace_source

console = Console()


def _fetch_module_from_marketplace(
    marketplace_name: str, module_name: str
) -> tuple[Path, dict]:
    """
    Fetch a module from a marketplace. Raises on error instead of SystemExit.

    Returns:
        Tuple of (module_path, module_metadata)
    """
    from lola.cli.mod import save_source_info
    from lola.parsers import fetch_module_named, detect_source_type

    ref_file = MARKET_DIR / f"{marketplace_name}.yml"

    if not ref_file.exists():
        raise ValueError(f"Marketplace '{marketplace_name}' not found")

    marketplace_ref = Marketplace.from_reference(ref_file)
    if not marketplace_ref.enabled:
        raise ValueError(f"Marketplace '{marketplace_name}' is disabled")

    cache_file = CACHE_DIR / f"{marketplace_name}.yml"
    if not cache_file.exists():
        raise ValueError(f"Marketplace '{marketplace_name}' cache not found")

    marketplace = Marketplace.from_cache(cache_file)

    module_dict = next(
        (m for m in marketplace.modules if m.get("name") == module_name), None
    )

    if not module_dict:
        raise ValueError(
            f"Module '{module_name}' not found in marketplace '{marketplace_name}'"
        )

    repository: str | None = module_dict.get("repository")
    if not repository:
        if not marketplace_ref.url:
            raise ValueError(
                f"Module '{module_name}' has no repository and marketplace has no source URL"
            )
        repository = resolve_marketplace_source(marketplace_ref.url)

    content_dirname = module_dict.get("path")

    source_type = detect_source_type(repository)
    module_path = fetch_module_named(
        repository, MODULES_DIR, module_name, content_dirname
    )
    save_source_info(module_path, repository, source_type, None)

    return module_path, module_dict


@click.group(name="bundle")
def bundle():
    """
    Manage lola bundles.

    Install, list, and inspect bundles from marketplaces.
    """
    pass


@bundle.command(name="install")
@click.argument("bundle_name", shell_complete=complete_bundle_names)
@click.option(
    "-a",
    "--assistant",
    type=click.Choice(list(TARGETS.keys())),
    default=None,
    help="AI assistant to install to (default: prompt interactively, or all in non-interactive mode)",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show detailed output",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Overwrite existing skills without prompting",
)
@click.argument("project_path", required=False, default="./")
def bundle_install_cmd(
    bundle_name: str,
    assistant: Optional[str],
    verbose: bool,
    force: bool,
    project_path: str,
):
    """
    Install all modules in a bundle.

    BUNDLE_NAME is the name of the bundle (e.g., 'teamA/engineer').

    \b
    Examples:
        lola bundle install teamA/engineer
        lola bundle install teamA/engineer -a claude-code
        lola bundle install platform/sre ./my-project
    """
    ensure_lola_dirs()

    mp_registry = MarketplaceRegistry(MARKET_DIR, CACHE_DIR)
    matches = mp_registry.search_bundle_all(bundle_name)

    if not matches:
        console.print(f"[red]Bundle '{bundle_name}' not found[/red]")
        console.print("[dim]Use 'lola bundle ls' to see available bundles[/dim]")
        raise SystemExit(1)

    if len(matches) == 1:
        bundle_data, marketplace_name = matches[0]
    else:
        compat_matches = [
            (
                {
                    "name": bundle_name,
                    "description": bd.get("description", ""),
                    "version": "",
                },
                mn,
            )
            for bd, mn in matches
        ]
        selected = mp_registry.select_marketplace(bundle_name, compat_matches)
        if selected is None:
            console.print("[yellow]Cancelled[/yellow]")
            raise SystemExit(130)
        bundle_data = next(bd for bd, mn in matches if mn == selected)
        marketplace_name = selected

    # Validate project path
    project_path = str(Path(project_path).resolve())
    if not Path(project_path).exists():
        from lola.exceptions import PathNotFoundError
        from lola.cli.utils import handle_lola_error

        handle_lola_error(PathNotFoundError(project_path, "Project path"))

    # Determine assistants
    if assistant:
        assistants_to_install = [assistant]
    elif is_interactive():
        chosen = select_assistants(list(TARGETS.keys()))
        if not chosen:
            console.print("[yellow]No assistants selected. Cancelled.[/yellow]")
            raise SystemExit(130)
        assistants_to_install = chosen
    else:
        assistants_to_install = list(TARGETS.keys())

    bundle_modules = bundle_data.get("modules", [])
    console.print(
        f"\n[bold]Installing bundle '{bundle_name}' ({len(bundle_modules)} modules) -> {project_path}[/bold]"
    )
    console.print()

    # Load marketplace sources for cross-marketplace resolution
    cache_file = CACHE_DIR / f"{marketplace_name}.yml"
    marketplace_sources: list[dict] = []
    if cache_file.exists():
        mp_cached = Marketplace.from_cache(cache_file)
        marketplace_sources = mp_cached.sources

    local_modules = get_local_modules_path(project_path)
    registry = get_registry()
    installed_count = 0
    skipped_count = 0
    failed_modules: list[tuple[str, str]] = []

    from lola.cli.mod import load_registered_module
    from lola.market.manager import parse_market_ref
    from lola.sync import parse_lolareq_line

    for i, entry in enumerate(bundle_modules):
        # Parse entry using lola-req syntax (supports version specs and @-refs)
        spec = parse_lolareq_line(entry, i + 1)
        if not spec:
            continue

        # Determine which marketplace to fetch from and the module name
        market_ref = parse_market_ref(spec.module_ref)
        if market_ref:
            mp_id, mod_name = market_ref
            resolved_mp = mp_registry.find_marketplace(mp_id)
            if not resolved_mp:
                resolved_mp = mp_registry.auto_add_source(mp_id, marketplace_sources)
            if not resolved_mp:
                console.print(
                    f"[red]Marketplace '{mp_id}' not found for '{entry}'[/red]"
                )
                failed_modules.append((entry, f"marketplace '{mp_id}' not found"))
                continue
            fetch_marketplace = resolved_mp
        else:
            mod_name = spec.module_ref
            fetch_marketplace = marketplace_name

        # Check if already installed for all requested assistants
        existing = registry.find(mod_name)
        already_installed = {
            inst.assistant
            for inst in existing
            if inst.scope == "project" and inst.project_path == project_path
        }
        needs_assistants = [
            a for a in assistants_to_install if a not in already_installed
        ]

        if not needs_assistants:
            if verbose:
                console.print(f"[dim]Skipping '{mod_name}' (already installed)[/dim]")
            skipped_count += 1
            continue

        module_path = MODULES_DIR / mod_name

        # Fetch from marketplace if not already registered
        module_dict: dict = {}
        if not module_path.exists():
            try:
                module_path, module_dict = _fetch_module_from_marketplace(
                    fetch_marketplace, mod_name
                )
                console.print(f"[green]Fetched '{mod_name}'[/green]")
            except Exception as e:
                console.print(f"[red]Failed to fetch '{mod_name}': {e}[/red]")
                failed_modules.append((mod_name, str(e)))
                continue

        # Version check if spec has version_spec
        fetched_version = module_dict.get("version") if module_dict else None
        if spec.version_spec and fetched_version:
            if not spec.matches_version(fetched_version):
                console.print(
                    f"[yellow]Warning: '{mod_name}' version {fetched_version} "
                    f"does not match {spec.version_spec}[/yellow]"
                )

        module = load_registered_module(module_path)
        if not module:
            console.print(f"[red]Failed to load '{mod_name}': invalid module[/red]")
            failed_modules.append((mod_name, "invalid module"))
            continue

        is_valid, errors = module.validate()
        if not is_valid:
            console.print(
                f"[red]Validation errors for '{mod_name}': {', '.join(errors)}[/red]"
            )
            failed_modules.append((mod_name, "validation failed"))
            continue

        # Resolve hooks
        marketplace_hooks = module_dict.get("hooks", {}) if module_dict else {}
        pre_install = marketplace_hooks.get("pre-install") or module.pre_install_hook
        post_install = marketplace_hooks.get("post-install") or module.post_install_hook

        succeeded_assistants = []
        for asst in needs_assistants:
            try:
                install_to_assistant(
                    module,
                    asst,
                    "project",
                    project_path,
                    local_modules,
                    registry,
                    verbose=verbose,
                    force=force,
                    pre_install_script=pre_install,
                    post_install_script=post_install,
                )
                succeeded_assistants.append(asst)
            except Exception as e:
                console.print(
                    f"[red]Failed to install '{mod_name}' to {asst}: {e}[/red]"
                )
                failed_modules.append((mod_name, f"install to {asst}: {e}"))

        if not succeeded_assistants:
            continue

        # Update version from marketplace metadata
        version = module_dict.get("version") if module_dict else None
        if version:
            for asst in succeeded_assistants:
                installations = registry.find(mod_name)
                for inst in installations:
                    if (
                        inst.assistant == asst
                        and inst.scope == "project"
                        and inst.project_path == project_path
                    ):
                        inst.version = version
                        registry.add(inst)

        installed_count += 1

    console.print()
    parts = [
        f"Installed {installed_count}/{len(bundle_modules)} modules to "
        f"{len(assistants_to_install)} assistant(s)"
    ]
    if skipped_count:
        parts.append(f"{skipped_count} already installed")
    console.print(f"[green]{', '.join(parts)}[/green]")

    if failed_modules:
        console.print()
        console.print("[bold red]Failures:[/bold red]")
        for mod_name, error in failed_modules:
            console.print(f"  [red]- {mod_name}: {error}[/red]")
        raise SystemExit(1)


@bundle.command(name="ls")
def bundle_ls_cmd():
    """
    List available bundles across marketplaces.

    Shows all bundles from enabled marketplaces.
    """
    mp_registry = MarketplaceRegistry(MARKET_DIR, CACHE_DIR)
    mp_registry.list_bundles()


@bundle.command(name="info")
@click.argument("bundle_name", shell_complete=complete_bundle_names)
def bundle_info_cmd(bundle_name: str):
    """
    Show details of a bundle.

    Displays the bundle's description and list of modules.
    """
    mp_registry = MarketplaceRegistry(MARKET_DIR, CACHE_DIR)
    mp_registry.show_bundle(bundle_name)
