"""
utils:
    Utility functions for lola package manager
"""

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from lola.config import LOLA_HOME, MODULES_DIR
from lola.exceptions import ConfigurationError


def ensure_lola_dirs():
    """Ensure the lola directories exist."""
    LOLA_HOME.mkdir(parents=True, exist_ok=True)
    MODULES_DIR.mkdir(parents=True, exist_ok=True)


def get_local_modules_path(project_path: Optional[str]) -> Path:
    """
    Get the path to .lola/modules/ for a given scope.

    Args:
        project_path: Project path (required)

    Returns:
        Path to .lola/modules/

    Raises:
        ConfigurationError: If project_path is not provided.
    """
    if not project_path:
        raise ConfigurationError("Project path is required (project-scope only)")
    return Path(project_path) / ".lola" / "modules"


def resolve_marketplace_source(marketplace_url: str) -> str:
    """Resolve a marketplace URL to a source path suitable for fetch_module.

    For file:// URIs and local file paths pointing to a .yml file,
    returns the parent directory. For git URLs, returns as-is.

    Raises:
        ValueError: If the URL cannot be resolved to a fetchable source.
    """
    parsed = urlparse(marketplace_url)
    if parsed.scheme == "file":
        path = Path(parsed.path)
        if path.suffix in (".yml", ".yaml"):
            return str(path.parent)
        return str(path)
    if not parsed.scheme and Path(marketplace_url).suffix in (".yml", ".yaml"):
        return str(Path(marketplace_url).parent)
    if parsed.scheme in ("http", "https"):
        path_lower = parsed.path.lower()
        if path_lower.endswith(".git") or "github.com" in marketplace_url or "gitlab.com" in marketplace_url:
            return marketplace_url
        raise ValueError(
            f"Cannot resolve marketplace URL '{marketplace_url}' as a module source. "
            "Modules without a 'repository' field can only be resolved from local or git-based marketplaces."
        )
    return marketplace_url
