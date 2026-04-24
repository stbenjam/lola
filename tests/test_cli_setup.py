"""Tests for the setup CLI command."""

from lola.cli.setup import setup_cmd


VALID_SKILL_MD = """---
name: test
description: Test skill
---
# Test Skill
"""

LOLA_YAML_SETUP = """setup:
  - name: tool-ok
    description: A tool that is always available
    check: "true"
    install: "echo installed"
  - name: tool-missing
    description: A tool that is never available
    check: "false"
    install: "echo installing"
"""

LOLA_YAML_ALL_OK = """setup:
  - name: tool-ok
    description: Always available
    check: "true"
"""


def _create_module_with_setup(modules_dir, name, lola_yaml_content):
    """Helper to create a module with setup deps in the modules dir."""
    module_dir = modules_dir / name
    module_dir.mkdir(parents=True, exist_ok=True)
    skills_dir = module_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)
    (module_dir / "lola.yaml").write_text(lola_yaml_content)
    return module_dir


class TestSetupCommand:
    """Tests for the setup command."""

    def test_setup_help(self, cli_runner):
        """Show setup help."""
        result = cli_runner.invoke(setup_cmd, ["--help"])
        assert result.exit_code == 0
        assert "Check and configure" in result.output

    def test_setup_check_all_satisfied(self, cli_runner, mock_lola_home):
        """--check exits 0 when all deps satisfied."""
        _create_module_with_setup(
            mock_lola_home["modules"], "test-mod", LOLA_YAML_ALL_OK
        )
        result = cli_runner.invoke(setup_cmd, ["test-mod", "--check"])
        assert result.exit_code == 0
        assert "ok" in result.output
        assert "1 of 1" in result.output

    def test_setup_check_some_missing(self, cli_runner, mock_lola_home):
        """--check exits 1 when some deps are missing."""
        _create_module_with_setup(
            mock_lola_home["modules"], "test-mod", LOLA_YAML_SETUP
        )
        result = cli_runner.invoke(setup_cmd, ["test-mod", "--check"])
        assert result.exit_code == 1
        assert "missing" in result.output
        assert "tool-missing" in result.output

    def test_setup_no_dependencies(self, cli_runner, mock_lola_home):
        """Module with no setup deps prints informational message."""
        module_dir = mock_lola_home["modules"] / "test-mod"
        module_dir.mkdir()
        skills_dir = module_dir / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

        result = cli_runner.invoke(setup_cmd, ["test-mod"])
        assert result.exit_code == 0
        assert "no setup dependencies" in result.output.lower()

    def test_setup_module_not_found(self, cli_runner, mock_lola_home):
        """Error for nonexistent module."""
        result = cli_runner.invoke(setup_cmd, ["nonexistent"])
        assert result.exit_code != 0

    def test_setup_specific_dep_satisfied(self, cli_runner, mock_lola_home):
        """--dep with satisfied dep exits 0."""
        _create_module_with_setup(
            mock_lola_home["modules"], "test-mod", LOLA_YAML_SETUP
        )
        result = cli_runner.invoke(setup_cmd, ["test-mod", "--dep", "tool-ok"])
        assert result.exit_code == 0
        assert "ok" in result.output

    def test_setup_specific_dep_missing_check(self, cli_runner, mock_lola_home):
        """--dep --check with missing dep exits 1."""
        _create_module_with_setup(
            mock_lola_home["modules"], "test-mod", LOLA_YAML_SETUP
        )
        result = cli_runner.invoke(
            setup_cmd, ["test-mod", "--dep", "tool-missing", "--check"]
        )
        assert result.exit_code == 1
        assert "missing" in result.output

    def test_setup_specific_dep_not_found(self, cli_runner, mock_lola_home):
        """--dep with nonexistent dep name errors."""
        _create_module_with_setup(
            mock_lola_home["modules"], "test-mod", LOLA_YAML_SETUP
        )
        result = cli_runner.invoke(
            setup_cmd, ["test-mod", "--dep", "nonexistent"]
        )
        assert result.exit_code == 1
        assert "nonexistent" in result.output


class TestSetupAll:
    """Tests for --all flag."""

    def test_all_check(self, cli_runner, mock_lola_home):
        """--all --check shows status of all modules."""
        _create_module_with_setup(
            mock_lola_home["modules"], "mod-a", LOLA_YAML_ALL_OK
        )
        _create_module_with_setup(
            mock_lola_home["modules"], "mod-b", LOLA_YAML_SETUP
        )
        result = cli_runner.invoke(setup_cmd, ["--all", "--check"])
        assert result.exit_code == 1  # mod-b has missing deps
        assert "mod-a" in result.output
        assert "mod-b" in result.output

    def test_all_no_modules_with_setup(self, cli_runner, mock_lola_home):
        """--all with no modules having setup deps."""
        module_dir = mock_lola_home["modules"] / "test-mod"
        module_dir.mkdir()
        skills_dir = module_dir / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

        result = cli_runner.invoke(setup_cmd, ["--all", "--check"])
        assert result.exit_code == 0
        assert "no modules" in result.output.lower()

    def test_all_check_all_satisfied(self, cli_runner, mock_lola_home):
        """--all --check exits 0 when all modules fully satisfied."""
        _create_module_with_setup(
            mock_lola_home["modules"], "mod-a", LOLA_YAML_ALL_OK
        )
        _create_module_with_setup(
            mock_lola_home["modules"], "mod-b", LOLA_YAML_ALL_OK
        )
        result = cli_runner.invoke(setup_cmd, ["--all", "--check"])
        assert result.exit_code == 0
