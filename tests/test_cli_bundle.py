"""Tests for the bundle CLI commands."""

from unittest.mock import patch, MagicMock

from lola.__main__ import main


class TestBundleGroup:
    """Tests for the bundle command group."""

    def test_bundle_help(self, cli_runner):
        """Bundle group shows help text."""
        result = cli_runner.invoke(main, ["bundle", "--help"])
        assert result.exit_code == 0
        assert "Manage lola bundles" in result.output

    def test_bundle_subcommands_listed(self, cli_runner):
        """Bundle group lists install, ls, and info subcommands."""
        result = cli_runner.invoke(main, ["bundle", "--help"])
        assert "install" in result.output
        assert "ls" in result.output
        assert "info" in result.output


class TestBundleLs:
    """Tests for 'lola bundle ls'."""

    def test_ls_no_marketplaces(self, cli_runner, tmp_path):
        """List bundles with no marketplaces registered."""
        market_dir = tmp_path / "market"
        cache_dir = market_dir / "cache"
        market_dir.mkdir(parents=True)
        cache_dir.mkdir(parents=True)

        with (
            patch("lola.cli.bundle.MARKET_DIR", market_dir),
            patch("lola.cli.bundle.CACHE_DIR", cache_dir),
        ):
            result = cli_runner.invoke(main, ["bundle", "ls"])

        assert result.exit_code == 0
        assert "No marketplaces registered" in result.output

    def test_ls_with_bundles(self, cli_runner, marketplace_with_bundles):
        """List bundles from marketplace."""
        market_dir = marketplace_with_bundles["market_dir"]
        cache_dir = marketplace_with_bundles["cache_dir"]

        with (
            patch("lola.cli.bundle.MARKET_DIR", market_dir),
            patch("lola.cli.bundle.CACHE_DIR", cache_dir),
        ):
            result = cli_runner.invoke(main, ["bundle", "ls"])

        assert result.exit_code == 0
        assert "teamA/engineer" in result.output
        assert "official" in result.output

    def test_ls_no_bundles_in_marketplaces(
        self, cli_runner, marketplace_with_modules
    ):
        """List bundles when marketplaces have no bundles."""
        market_dir = marketplace_with_modules["market_dir"]
        cache_dir = marketplace_with_modules["cache_dir"]

        with (
            patch("lola.cli.bundle.MARKET_DIR", market_dir),
            patch("lola.cli.bundle.CACHE_DIR", cache_dir),
        ):
            result = cli_runner.invoke(main, ["bundle", "ls"])

        assert result.exit_code == 0
        assert "No bundles found" in result.output


class TestBundleInfo:
    """Tests for 'lola bundle info'."""

    def test_info_found(self, cli_runner, marketplace_with_bundles):
        """Show bundle details."""
        market_dir = marketplace_with_bundles["market_dir"]
        cache_dir = marketplace_with_bundles["cache_dir"]

        with (
            patch("lola.cli.bundle.MARKET_DIR", market_dir),
            patch("lola.cli.bundle.CACHE_DIR", cache_dir),
        ):
            result = cli_runner.invoke(main, ["bundle", "info", "teamA/engineer"])

        assert result.exit_code == 0
        assert "teamA/engineer" in result.output
        assert "Standard engineer toolkit" in result.output
        assert "git-workflow" in result.output
        assert "code-review" in result.output

    def test_info_not_found(self, cli_runner, marketplace_with_bundles):
        """Show error for non-existent bundle."""
        market_dir = marketplace_with_bundles["market_dir"]
        cache_dir = marketplace_with_bundles["cache_dir"]

        with (
            patch("lola.cli.bundle.MARKET_DIR", market_dir),
            patch("lola.cli.bundle.CACHE_DIR", cache_dir),
        ):
            result = cli_runner.invoke(main, ["bundle", "info", "nonexistent/bundle"])

        assert result.exit_code == 0
        assert "not found" in result.output


class TestBundleInstall:
    """Tests for 'lola bundle install'."""

    def test_install_bundle_not_found(self, cli_runner, tmp_path):
        """Bundle not found exits with error."""
        market_dir = tmp_path / "market"
        cache_dir = market_dir / "cache"
        market_dir.mkdir(parents=True)
        cache_dir.mkdir(parents=True)

        with (
            patch("lola.cli.bundle.MARKET_DIR", market_dir),
            patch("lola.cli.bundle.CACHE_DIR", cache_dir),
            patch("lola.cli.bundle.ensure_lola_dirs"),
        ):
            result = cli_runner.invoke(
                main, ["bundle", "install", "nonexistent/bundle"]
            )

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_install_bundle_success(
        self, cli_runner, tmp_path, marketplace_with_bundles
    ):
        """Install bundle fetches and installs all modules."""
        market_dir = marketplace_with_bundles["market_dir"]
        cache_dir = marketplace_with_bundles["cache_dir"]
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create mock modules in MODULES_DIR after "fetch"
        for mod_name in ["git-workflow", "code-review"]:
            mod_dir = modules_dir / mod_name
            mod_dir.mkdir()
            skills_dir = mod_dir / "skills" / "skill1"
            skills_dir.mkdir(parents=True)
            (skills_dir / "SKILL.md").write_text(
                f"---\ndescription: {mod_name} skill\n---\n\nSkill content.\n"
            )

        mock_registry = MagicMock()
        mock_registry.find.return_value = []

        with (
            patch("lola.cli.bundle.MARKET_DIR", market_dir),
            patch("lola.cli.bundle.CACHE_DIR", cache_dir),
            patch("lola.cli.bundle.MODULES_DIR", modules_dir),
            patch("lola.cli.bundle.ensure_lola_dirs"),
            patch("lola.cli.bundle.get_registry", return_value=mock_registry),
            patch("lola.cli.bundle.is_interactive", return_value=False),
            patch("lola.cli.bundle.install_to_assistant") as mock_install,
        ):
            result = cli_runner.invoke(
                main,
                [
                    "bundle",
                    "install",
                    "teamA/engineer",
                    "-a",
                    "claude-code",
                    str(project_dir),
                ],
            )

        assert result.exit_code == 0
        assert "Installed 2/2 modules" in result.output
        assert mock_install.call_count == 2

    def test_install_bundle_partial_failure(
        self, cli_runner, tmp_path, marketplace_with_bundles
    ):
        """Partial failure continues with remaining modules."""
        market_dir = marketplace_with_bundles["market_dir"]
        cache_dir = marketplace_with_bundles["cache_dir"]
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Only create one module — the other will fail to load
        mod_dir = modules_dir / "git-workflow"
        mod_dir.mkdir()
        skills_dir = mod_dir / "skills" / "skill1"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\ndescription: skill\n---\n\nContent.\n"
        )

        # code-review doesn't exist and fetch will fail
        mock_registry = MagicMock()
        mock_registry.find.return_value = []

        def mock_fetch(marketplace_name, module_name):
            raise ValueError("fetch failed")

        with (
            patch("lola.cli.bundle.MARKET_DIR", market_dir),
            patch("lola.cli.bundle.CACHE_DIR", cache_dir),
            patch("lola.cli.bundle.MODULES_DIR", modules_dir),
            patch("lola.cli.bundle.ensure_lola_dirs"),
            patch("lola.cli.bundle.get_registry", return_value=mock_registry),
            patch("lola.cli.bundle.is_interactive", return_value=False),
            patch("lola.cli.bundle.install_to_assistant"),
            patch(
                "lola.cli.bundle._fetch_module_from_marketplace",
                side_effect=mock_fetch,
            ),
        ):
            result = cli_runner.invoke(
                main,
                [
                    "bundle",
                    "install",
                    "teamA/engineer",
                    "-a",
                    "claude-code",
                    str(project_dir),
                ],
            )

        assert result.exit_code == 1
        assert "Installed 1/2 modules" in result.output
        assert "Failures:" in result.output

    def test_install_help(self, cli_runner):
        """Install subcommand shows help."""
        result = cli_runner.invoke(main, ["bundle", "install", "--help"])
        assert result.exit_code == 0
        assert "BUNDLE_NAME" in result.output
