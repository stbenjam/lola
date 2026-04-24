"""Tests for the utils module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from lola.exceptions import ConfigurationError
from lola.utils import (
    ensure_lola_dirs,
    get_local_modules_path,
    resolve_marketplace_source,
)


class TestEnsureLolaDir:
    """Tests for ensure_lola_dirs()."""

    def test_creates_directories(self, tmp_path):
        """Creates LOLA_HOME and MODULES_DIR if they don't exist."""
        lola_home = tmp_path / ".lola"
        modules_dir = lola_home / "modules"

        with (
            patch("lola.utils.LOLA_HOME", lola_home),
            patch("lola.utils.MODULES_DIR", modules_dir),
        ):
            ensure_lola_dirs()

        assert lola_home.exists()
        assert modules_dir.exists()

    def test_idempotent(self, tmp_path):
        """Calling multiple times doesn't cause errors."""
        lola_home = tmp_path / ".lola"
        modules_dir = lola_home / "modules"

        with (
            patch("lola.utils.LOLA_HOME", lola_home),
            patch("lola.utils.MODULES_DIR", modules_dir),
        ):
            ensure_lola_dirs()
            ensure_lola_dirs()  # Should not raise

        assert lola_home.exists()
        assert modules_dir.exists()

    def test_preserves_existing_content(self, tmp_path):
        """Existing content is preserved."""
        lola_home = tmp_path / ".lola"
        modules_dir = lola_home / "modules"
        lola_home.mkdir(parents=True)
        modules_dir.mkdir()

        # Create some content
        test_file = lola_home / "test.txt"
        test_file.write_text("existing content")

        with (
            patch("lola.utils.LOLA_HOME", lola_home),
            patch("lola.utils.MODULES_DIR", modules_dir),
        ):
            ensure_lola_dirs()

        assert test_file.exists()
        assert test_file.read_text() == "existing content"


class TestGetLocalModulesPath:
    """Tests for get_local_modules_path()."""

    def test_project_scope(self, tmp_path):
        """Get modules path for project scope."""
        project = tmp_path / "myproject"
        project.mkdir()

        path = get_local_modules_path(str(project))

        assert path == project / ".lola" / "modules"

    def test_user_scope(self):
        """User scope is not supported (project-only)."""
        with pytest.raises(ConfigurationError, match="Project path is required"):
            get_local_modules_path(None)

    def test_empty_string_treated_as_falsy(self):
        """Empty string is not a valid project path."""
        with pytest.raises(ConfigurationError, match="Project path is required"):
            get_local_modules_path("")

    def test_returns_path_object(self, tmp_path):
        """Returns a Path object."""
        path = get_local_modules_path(str(tmp_path))

        assert isinstance(path, Path)


class TestResolveMarketplaceSource:
    """Tests for resolve_marketplace_source()."""

    def test_file_uri_yml(self):
        """file:// URI pointing to .yml returns parent directory."""
        result = resolve_marketplace_source("file:///home/user/repo/market.yml")
        assert result == "/home/user/repo"

    def test_file_uri_directory(self):
        """file:// URI pointing to directory returns as-is."""
        result = resolve_marketplace_source("file:///home/user/repo")
        assert result == "/home/user/repo"

    def test_local_path_yml(self):
        """Local path to .yml returns parent directory."""
        result = resolve_marketplace_source("/home/user/repo/market.yaml")
        assert result == "/home/user/repo"

    def test_local_path_non_yml(self):
        """Local path without .yml suffix returns as-is."""
        result = resolve_marketplace_source("/home/user/repo")
        assert result == "/home/user/repo"

    def test_git_url_passes_through(self):
        """Git URL passes through unchanged."""
        url = "https://github.com/org/repo.git"
        assert resolve_marketplace_source(url) == url

    def test_github_url_passes_through(self):
        """GitHub URL without .git suffix passes through."""
        url = "https://github.com/org/repo"
        assert resolve_marketplace_source(url) == url

    def test_gitlab_url_passes_through(self):
        """GitLab URL passes through."""
        url = "https://gitlab.com/org/repo.git"
        assert resolve_marketplace_source(url) == url

    def test_https_non_git_url_raises(self):
        """HTTPS URL that isn't a git repo raises ValueError."""
        with pytest.raises(ValueError, match="Cannot resolve marketplace URL"):
            resolve_marketplace_source("https://example.com/catalog.yml")
