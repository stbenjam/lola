"""
market.manager:
    Marketplace registry management for adding, updating, and managing
    marketplace catalogs
"""

from pathlib import Path
from rich.console import Console
from rich.table import Table
import yaml

from lola.models import Marketplace
from lola.market.search import search_market, display_market
from lola.exceptions import MarketplaceNameError
from lola.prompts import select_marketplace as prompt_select_marketplace


def parse_market_ref(module_name: str) -> tuple[str, str] | None:
    """
    Parse marketplace reference from module name.

    Supports both short IDs and Go-style canonical paths:
        @common/ci          → ("common", "ci")
        @github.com/org/repo/ci → ("github.com/org/repo", "ci")

    The last path segment is always the module name; everything before it
    is the marketplace identifier.

    Returns:
        Tuple of (marketplace_identifier, module_name) or None.
    """
    if not module_name.startswith("@") or "/" not in module_name:
        return None
    path = module_name[1:]
    last_slash = path.rfind("/")
    marketplace_id = path[:last_slash]
    mod_name = path[last_slash + 1:]
    if not marketplace_id or not mod_name:
        return None
    return marketplace_id, mod_name


def validate_marketplace_name(name: str) -> str:
    """Validate marketplace name to ensure it's a valid filesystem name.

    Raises:
        MarketplaceNameError: If the name is invalid.

    Returns:
        The validated name.
    """
    if not name:
        raise MarketplaceNameError(name, "name cannot be empty")
    if name in (".", ".."):
        raise MarketplaceNameError(name, "path traversal not allowed")
    if "/" in name or "\\" in name:
        raise MarketplaceNameError(name, "path separators not allowed")
    if name.startswith("."):
        raise MarketplaceNameError(name, "cannot start with dot")

    return name


class MarketplaceRegistry:
    """Manages marketplace references and caches."""

    def __init__(self, market_dir: Path, cache_dir: Path):
        """Initialize registry."""
        self.market_dir = market_dir
        self.cache_dir = cache_dir
        self.console = Console()

        self.market_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def add(self, name: str, url: str) -> None:
        """Add a new marketplace."""
        try:
            name = validate_marketplace_name(name)
        except MarketplaceNameError as e:
            self.console.print(f"[red]{e}[/red]")
            return

        ref_file = self.market_dir / f"{name}.yml"

        if ref_file.exists():
            self.console.print(f"[yellow]Marketplace '{name}' already exists[/yellow]")
            return

        try:
            marketplace = Marketplace.from_url(url, name)
            is_valid, errors = marketplace.validate()

            if not is_valid:
                self.console.print("[red]Validation failed:[/red]")
                for err in errors:
                    self.console.print(f"  - {err}")
                return

            # Save reference
            with open(ref_file, "w") as f:
                yaml.dump(marketplace.to_reference_dict(), f)

            # Save cache
            cache_file = self.cache_dir / f"{name}.yml"
            with open(cache_file, "w") as f:
                yaml.dump(marketplace.to_cache_dict(), f)

            module_count = len(marketplace.modules)
            self.console.print(
                f"[green]Added marketplace '{name}' with {module_count} modules[/green]"
            )
        except ValueError as e:
            self.console.print(f"[red]Error: {e}[/red]")

    def find_marketplace(self, identifier: str) -> str | None:
        """Find a registered marketplace by declared id, canonical_id, or user-chosen name.

        Returns the user-chosen name (filename stem) used for file lookups, or None.
        """
        for ref_file in self.market_dir.glob("*.yml"):
            mp = Marketplace.from_reference(ref_file)
            user_name = ref_file.stem
            if identifier == user_name:
                return user_name
            if mp.id and identifier == mp.id:
                return user_name
            if mp.canonical_id and identifier == mp.canonical_id:
                return user_name
        return None

    def auto_add_source(self, identifier: str, sources: list[dict]) -> str | None:
        """Auto-add a marketplace from a sources list.

        Searches `sources` for an entry matching `identifier` by `id` or
        canonical URL path. If found and not already registered, adds it.

        Returns the marketplace name if added/found, None otherwise.
        """
        for source in sources:
            src_id = source.get("id", "")
            src_url = source.get("url", "")
            src_canonical = Marketplace._url_to_canonical_id(src_url) if src_url else ""

            if identifier not in (src_id, src_canonical):
                continue

            # Use the source id as the marketplace name
            mp_name = src_id or src_canonical.replace("/", "-")
            if not mp_name:
                continue

            # Already registered?
            existing = self.find_marketplace(mp_name)
            if existing:
                return existing

            self.console.print(
                f"[dim]Auto-adding marketplace '{mp_name}' from {src_url}[/dim]"
            )
            self.add(mp_name, src_url)

            # Verify it was actually added
            ref_file = self.market_dir / f"{mp_name}.yml"
            if ref_file.exists():
                return mp_name
            return None

        return None

    def search_module(self, module_name: str) -> tuple[dict, str] | None:
        """
        Search for a module by name across all enabled marketplaces.

        Args:
            module_name: Name of the module to search for

        Returns:
            Tuple of (module_dict, marketplace_name) if found, None otherwise
        """
        # Iterate through all marketplace reference files
        for ref_file in self.market_dir.glob("*.yml"):
            # Load reference to check if marketplace is enabled
            marketplace_ref = Marketplace.from_reference(ref_file)

            if not marketplace_ref.enabled:
                continue

            # Load cache to get modules
            cache_file = self.cache_dir / ref_file.name
            if not cache_file.exists():
                continue

            marketplace = Marketplace.from_cache(cache_file)

            # Search for module in this marketplace
            for module in marketplace.modules:
                if module.get("name") == module_name:
                    return module, marketplace_ref.name

        return None

    def search_module_all(self, module_name: str) -> list[tuple[dict, str]]:
        """
        Search for a module by name across all enabled marketplaces.

        Returns all matches, not just the first one.

        Args:
            module_name: Name of the module to search for

        Returns:
            List of tuples (module_dict, marketplace_name)
        """
        matches = []

        for ref_file in self.market_dir.glob("*.yml"):
            marketplace_ref = Marketplace.from_reference(ref_file)

            if not marketplace_ref.enabled:
                continue

            cache_file = self.cache_dir / ref_file.name
            if not cache_file.exists():
                continue

            marketplace = Marketplace.from_cache(cache_file)

            for module in marketplace.modules:
                if module.get("name") == module_name:
                    matches.append((module, marketplace_ref.name))

        return matches

    def select_marketplace(
        self,
        module_name: str,
        matches: list[tuple[dict, str]],
        show_version: bool = True,
    ) -> str | None:
        """
        Prompt user to select a marketplace when multiple matches exist.

        Args:
            module_name: Name of the module
            matches: List of (module_dict, marketplace_name) tuples
            show_version: Whether to display version in the options (unused,
                kept for backwards-compatible signature)

        Returns:
            Selected marketplace name, or None if cancelled
        """
        if not matches:
            return None

        if len(matches) == 1:
            return matches[0][1]

        return prompt_select_marketplace(matches)

    def search_bundle(self, bundle_name: str) -> tuple[dict, str] | None:
        """
        Search for a bundle by name across all enabled marketplaces.

        Returns:
            Tuple of (bundle_dict, marketplace_name) if found, None otherwise
        """
        for ref_file in self.market_dir.glob("*.yml"):
            marketplace_ref = Marketplace.from_reference(ref_file)

            if not marketplace_ref.enabled:
                continue

            cache_file = self.cache_dir / ref_file.name
            if not cache_file.exists():
                continue

            marketplace = Marketplace.from_cache(cache_file)

            if bundle_name in marketplace.bundles:
                return marketplace.bundles[bundle_name], marketplace_ref.name

        return None

    def search_bundle_all(self, bundle_name: str) -> list[tuple[dict, str]]:
        """
        Search for a bundle by name across all enabled marketplaces.

        Returns all matches, not just the first one.
        """
        matches = []

        for ref_file in self.market_dir.glob("*.yml"):
            marketplace_ref = Marketplace.from_reference(ref_file)

            if not marketplace_ref.enabled:
                continue

            cache_file = self.cache_dir / ref_file.name
            if not cache_file.exists():
                continue

            marketplace = Marketplace.from_cache(cache_file)

            if bundle_name in marketplace.bundles:
                matches.append((marketplace.bundles[bundle_name], marketplace_ref.name))

        return matches

    def list_bundles(self) -> None:
        """List all bundles across enabled marketplaces."""
        ref_files = list(self.market_dir.glob("*.yml"))

        if not ref_files:
            self.console.print("[yellow]No marketplaces registered[/yellow]")
            self.console.print(
                "[dim]Use 'lola market add <name> <url>' to add a marketplace[/dim]"
            )
            return

        has_bundles = False
        table = Table(show_header=True, header_style="bold")
        table.add_column("Bundle")
        table.add_column("Modules", justify="right")
        table.add_column("Marketplace")
        table.add_column("Description")

        for ref_file in sorted(ref_files):
            marketplace_ref = Marketplace.from_reference(ref_file)
            if not marketplace_ref.enabled:
                continue

            cache_file = self.cache_dir / ref_file.name
            if not cache_file.exists():
                continue

            marketplace = Marketplace.from_cache(cache_file)

            for bundle_name, bundle_data in sorted(marketplace.bundles.items()):
                has_bundles = True
                module_count = len(bundle_data.get("modules", []))
                description = bundle_data.get("description", "")
                table.add_row(
                    bundle_name,
                    str(module_count),
                    marketplace_ref.name,
                    description,
                )

        if has_bundles:
            self.console.print(table)
        else:
            self.console.print(
                "[yellow]No bundles found across marketplaces[/yellow]"
            )

    def show_bundle(self, bundle_name: str) -> None:
        """Show details of a bundle."""
        matches = self.search_bundle_all(bundle_name)

        if not matches:
            self.console.print(f"[red]Bundle '{bundle_name}' not found[/red]")
            self.console.print(
                "[dim]Use 'lola bundle ls' to see available bundles[/dim]"
            )
            return

        if len(matches) == 1:
            bundle_data, marketplace_name = matches[0]
        else:
            # Reuse select_marketplace with compatible dict shape
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
            selected = self.select_marketplace(bundle_name, compat_matches)
            if selected is None:
                return
            bundle_data = next(bd for bd, mn in matches if mn == selected)
            marketplace_name = selected

        self.console.print(f"[bold]{bundle_name}[/bold]")
        description = bundle_data.get("description", "")
        if description:
            self.console.print(f"[dim]  {description}[/dim]")
        self.console.print(f"[dim]  Marketplace: {marketplace_name}[/dim]")
        self.console.print()

        # Load marketplace to look up module descriptions
        cache_file = self.cache_dir / f"{marketplace_name}.yml"
        module_descriptions: dict[str, str] = {}
        if cache_file.exists():
            marketplace = Marketplace.from_cache(cache_file)
            for mod in marketplace.modules:
                module_descriptions[mod.get("name", "")] = mod.get("description", "")

        bundle_modules = bundle_data.get("modules", [])
        if not bundle_modules:
            self.console.print("[yellow]No modules in this bundle[/yellow]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("Module")
        table.add_column("Description")

        for mod_name in bundle_modules:
            table.add_row(mod_name, module_descriptions.get(mod_name, ""))

        self.console.print(table)

    def search(self, query: str) -> None:
        """Search for modules across all enabled marketplaces."""
        ref_files = list(self.market_dir.glob("*.yml"))

        if not ref_files:
            self.console.print("[yellow]No marketplaces registered[/yellow]")
            self.console.print(
                "[dim]Use 'lola market add <name> <url>' to add a marketplace[/dim]"
            )
            return

        results = search_market(query, self.market_dir, self.cache_dir)
        display_market(results, query, self.console)

    def list(self) -> None:
        """List all registered marketplaces."""
        ref_files = list(self.market_dir.glob("*.yml"))

        if not ref_files:
            self.console.print("[yellow]No marketplaces registered[/yellow]")
            self.console.print(
                "[dim]Use 'lola market add <name> <url>' to add a marketplace[/dim]"
            )
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("Name")
        table.add_column("Modules", justify="right")
        table.add_column("Status")

        for ref_file in sorted(ref_files):
            marketplace_ref = Marketplace.from_reference(ref_file)

            cache_file = self.cache_dir / ref_file.name
            module_count = 0
            if cache_file.exists():
                marketplace = Marketplace.from_cache(cache_file)
                module_count = len(marketplace.modules)

            status = "[red]disabled[/red]"
            if marketplace_ref.enabled:
                status = "[green]enabled[/green]"

            table.add_row(marketplace_ref.name, str(module_count), status)

        self.console.print(table)

    def show(self, name: str) -> None:
        """Show modules in a specific marketplace."""
        ref_file = self.market_dir / f"{name}.yml"

        if not ref_file.exists():
            self.console.print(f"[red]Marketplace '{name}' not found[/red]")
            return

        cache_file = self.cache_dir / f"{name}.yml"
        if not cache_file.exists():
            self.console.print(
                f"[yellow]Cache missing for '{name}', fetching...[/yellow]"
            )
            if not self.update_one(name):
                return
            cache_file = self.cache_dir / f"{name}.yml"

        marketplace_ref = Marketplace.from_reference(ref_file)
        marketplace = Marketplace.from_cache(cache_file)

        # Display marketplace header
        status = (
            "[green]enabled[/green]"
            if marketplace_ref.enabled
            else "[red]disabled[/red]"
        )
        self.console.print(f"[bold]{marketplace.name}[/bold] ({status})")
        if marketplace.description and marketplace.description != marketplace.name:
            self.console.print(f"[dim]  {marketplace.description}[/dim]")
        if marketplace.version:
            self.console.print(f"[dim]  Version {marketplace.version}[/dim]")
        self.console.print()

        if not marketplace.modules:
            self.console.print("[yellow]No modules in this marketplace[/yellow]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("Module")
        table.add_column("Version")
        table.add_column("Description")
        table.add_column("Tags")

        for module in sorted(marketplace.modules, key=lambda m: m.get("name", "")):
            tags = ", ".join(module.get("tags", []))
            table.add_row(
                module.get("name", ""),
                module.get("version", ""),
                module.get("description", ""),
                tags,
            )

        self.console.print(table)

    def _set_enabled(self, name: str, enabled: bool) -> None:
        """Set marketplace enabled status."""
        ref_file = self.market_dir / f"{name}.yml"

        if not ref_file.exists():
            self.console.print(f"[red]Marketplace '{name}' not found[/red]")
            return

        marketplace_ref = Marketplace.from_reference(ref_file)
        marketplace_ref.enabled = enabled

        with open(ref_file, "w") as f:
            yaml.dump(marketplace_ref.to_reference_dict(), f)

        status = "enabled" if enabled else "disabled"
        self.console.print(f"[green]Marketplace '{name}' {status}[/green]")

    def enable(self, name: str) -> None:
        """Enable a marketplace."""
        self._set_enabled(name, True)

    def disable(self, name: str) -> None:
        """Disable a marketplace."""
        self._set_enabled(name, False)

    def remove(self, name: str) -> None:
        """Remove a marketplace."""
        ref_file = self.market_dir / f"{name}.yml"

        if not ref_file.exists():
            self.console.print(f"[red]Marketplace '{name}' not found[/red]")
            return

        cache_file = self.cache_dir / f"{name}.yml"

        ref_file.unlink()
        if cache_file.exists():
            cache_file.unlink()

        self.console.print(f"[green]Removed marketplace '{name}'[/green]")

    def update_one(self, name: str) -> bool:
        """Update cache for a single marketplace."""
        ref_file = self.market_dir / f"{name}.yml"

        if not ref_file.exists():
            self.console.print(f"[red]Marketplace '{name}' not found[/red]")
            return False

        marketplace_ref = Marketplace.from_reference(ref_file)

        try:
            marketplace = Marketplace.from_url(marketplace_ref.url, name)
            is_valid, errors = marketplace.validate()

            if not is_valid:
                self.console.print(f"[red]Validation failed for '{name}':[/red]")
                for err in errors:
                    self.console.print(f"  - {err}")
                return False

            cache_file = self.cache_dir / f"{name}.yml"
            with open(cache_file, "w") as f:
                yaml.dump(marketplace.to_cache_dict(), f)

            module_count = len(marketplace.modules)
            self.console.print(
                f"[green]Updated '{name}' with {module_count} modules[/green]"
            )
            return True
        except ValueError as e:
            self.console.print(f"[red]Failed to update '{name}': {e}[/red]")
            return False

    def update(self, name: str | None = None) -> None:
        """Update marketplace cache(s)."""
        if name:
            self.update_one(name)
            return

        ref_files = list(self.market_dir.glob("*.yml"))
        if not ref_files:
            self.console.print("[yellow]No marketplaces registered[/yellow]")
            return

        success_count = 0
        for ref_file in sorted(ref_files):
            marketplace_ref = Marketplace.from_reference(ref_file)
            if self.update_one(marketplace_ref.name):
                success_count += 1

        total = len(ref_files)
        self.console.print(
            f"[green]Updated {success_count}/{total} marketplaces[/green]"
        )
