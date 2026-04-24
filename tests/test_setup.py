"""Tests for setup dependency checking and execution."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from lola.models import Module, SetupDependency
from lola.setup import check_dependency, run_setup, check_all


VALID_SKILL_MD = """---
name: test
description: Test skill
---
# Test Skill
"""


@pytest.fixture
def module_with_setup(tmp_path):
    """Create a module with setup dependencies."""
    module_dir = tmp_path / "test-module"
    module_dir.mkdir()

    skills_dir = module_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

    scripts_dir = module_dir / "scripts" / "setup"
    scripts_dir.mkdir(parents=True)
    check_script = scripts_dir / "check-gh.sh"
    check_script.write_text("#!/bin/bash\nwhich gh && gh auth status\n")
    check_script.chmod(0o755)

    install_script = scripts_dir / "install-gh.sh"
    install_script.write_text("#!/bin/bash\necho 'installing gh'\n")
    install_script.chmod(0o755)

    lola_yaml = module_dir / "lola.yaml"
    lola_yaml.write_text("""setup:
  - name: gh
    description: GitHub CLI for PR operations
    check: which gh
    install: scripts/setup/install-gh.sh
  - name: python3
    description: Python 3 runtime
    check: python3 --version
""")

    module = Module.from_path(module_dir)
    assert module is not None
    return module


@pytest.fixture
def module_with_check_script(tmp_path):
    """Create a module with a check script file (not inline command)."""
    module_dir = tmp_path / "test-module"
    module_dir.mkdir()

    skills_dir = module_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

    scripts_dir = module_dir / "scripts"
    scripts_dir.mkdir()
    check_script = scripts_dir / "check.sh"
    check_script.write_text("#!/bin/bash\nexit 0\n")
    check_script.chmod(0o755)

    lola_yaml = module_dir / "lola.yaml"
    lola_yaml.write_text("""setup:
  - name: custom-check
    description: Custom check via script
    check: scripts/check.sh
""")

    module = Module.from_path(module_dir)
    assert module is not None
    return module


# --- Model parsing tests ---


def test_module_with_setup_dependencies(module_with_setup):
    """Setup dependencies are parsed from lola.yaml."""
    assert len(module_with_setup.setup) == 2
    assert module_with_setup.setup[0].name == "gh"
    assert module_with_setup.setup[0].description == "GitHub CLI for PR operations"
    assert module_with_setup.setup[0].check == "which gh"
    assert module_with_setup.setup[0].install == "scripts/setup/install-gh.sh"
    assert module_with_setup.setup[1].name == "python3"
    assert module_with_setup.setup[1].install is None


def test_module_without_setup(tmp_path):
    """Module without setup section has empty setup list."""
    module_dir = tmp_path / "test-module"
    module_dir.mkdir()
    skills_dir = module_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

    module = Module.from_path(module_dir)
    assert module is not None
    assert module.setup == []


def test_module_with_malformed_setup(tmp_path):
    """Malformed setup entries are skipped."""
    module_dir = tmp_path / "test-module"
    module_dir.mkdir()
    skills_dir = module_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

    lola_yaml = module_dir / "lola.yaml"
    lola_yaml.write_text("""setup:
  - name: valid
    check: which valid
    description: Valid entry
  - description: missing name and check
  - not-a-dict
  - name: missing-check
    description: no check field
""")

    module = Module.from_path(module_dir)
    assert module is not None
    assert len(module.setup) == 1
    assert module.setup[0].name == "valid"


def test_validate_setup_path_traversal(tmp_path):
    """Validation catches install scripts outside module directory."""
    module_dir = tmp_path / "test-module"
    module_dir.mkdir()
    skills_dir = module_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

    # Create a script outside the module
    outside_script = tmp_path / "evil.sh"
    outside_script.write_text("#!/bin/bash\necho evil\n")

    lola_yaml = module_dir / "lola.yaml"
    lola_yaml.write_text("""setup:
  - name: evil
    description: Path traversal attempt
    check: echo ok
    install: ../evil.sh
""")

    module = Module.from_path(module_dir)
    assert module is not None
    is_valid, errors = module.validate()
    assert not is_valid
    assert any("outside module directory" in e for e in errors)


# --- check_dependency tests ---


def test_check_dependency_satisfied(module_with_setup):
    """Check command that exits 0 returns satisfied."""
    dep = SetupDependency(
        name="test", description="test", check="true"
    )
    ok, msg = check_dependency(dep, module_with_setup)
    assert ok is True


def test_check_dependency_not_satisfied(module_with_setup):
    """Check command that exits non-zero returns unsatisfied."""
    dep = SetupDependency(
        name="test", description="test", check="false"
    )
    ok, msg = check_dependency(dep, module_with_setup)
    assert ok is False


def test_check_dependency_inline_command(module_with_setup):
    """Inline shell commands work (not file paths)."""
    dep = SetupDependency(
        name="test", description="test", check="echo hello && true"
    )
    ok, msg = check_dependency(dep, module_with_setup)
    assert ok is True
    assert "hello" in msg


def test_check_dependency_script_path(module_with_check_script):
    """Check via script file works."""
    dep = module_with_check_script.setup[0]
    ok, msg = check_dependency(dep, module_with_check_script)
    assert ok is True


def test_check_dependency_timeout(module_with_setup):
    """Check command that hangs returns unsatisfied."""
    dep = SetupDependency(
        name="test", description="test", check="sleep 100"
    )
    with patch("lola.setup.subprocess.run", side_effect=subprocess.TimeoutExpired("bash", 30)):
        ok, msg = check_dependency(dep, module_with_setup)
    assert ok is False
    assert "timed out" in msg


# --- run_setup tests ---


def test_run_setup_success(module_with_setup):
    """Install script that exits 0 returns True."""
    dep = module_with_setup.setup[0]  # has install script
    ok = run_setup(dep, module_with_setup)
    assert ok is True


def test_run_setup_failure(tmp_path):
    """Install script that exits non-zero returns False."""
    module_dir = tmp_path / "test-module"
    module_dir.mkdir()
    skills_dir = module_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

    scripts_dir = module_dir / "scripts"
    scripts_dir.mkdir()
    fail_script = scripts_dir / "fail.sh"
    fail_script.write_text("#!/bin/bash\nexit 1\n")
    fail_script.chmod(0o755)

    lola_yaml = module_dir / "lola.yaml"
    lola_yaml.write_text("""setup:
  - name: failing
    description: Always fails
    check: false
    install: scripts/fail.sh
""")

    module = Module.from_path(module_dir)
    assert module is not None
    dep = module.setup[0]
    ok = run_setup(dep, module)
    assert ok is False


def test_run_setup_no_install(module_with_setup):
    """Dep without install script returns False."""
    dep = module_with_setup.setup[1]  # python3, no install
    ok = run_setup(dep, module_with_setup)
    assert ok is False


def test_run_setup_inline_command(module_with_setup):
    """Inline install command works."""
    dep = SetupDependency(
        name="test", description="test", check="true", install="echo installed"
    )
    ok = run_setup(dep, module_with_setup)
    assert ok is True


def test_run_setup_path_traversal(tmp_path):
    """Path traversal in install script is blocked at runtime."""
    module_dir = tmp_path / "test-module"
    module_dir.mkdir()
    skills_dir = module_dir / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(VALID_SKILL_MD)

    outside_script = tmp_path / "evil.sh"
    outside_script.write_text("#!/bin/bash\necho evil\n")

    module = Module.from_path(module_dir)
    assert module is not None
    dep = SetupDependency(
        name="evil", description="evil", check="true", install="../evil.sh"
    )
    with pytest.raises(ValueError, match="outside module directory"):
        run_setup(dep, module)


def test_run_setup_env_vars(module_with_setup):
    """Setup script receives correct environment variables."""
    dep = module_with_setup.setup[0]
    with patch("lola.setup.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_setup(dep, module_with_setup)
        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs["env"]
        assert env["LOLA_MODULE_NAME"] == "test-module"
        assert env["LOLA_SETUP_NAME"] == "gh"
        assert "LOLA_MODULE_PATH" in env


# --- check_all tests ---


def test_check_all(module_with_setup):
    """check_all returns results for all dependencies."""
    results = check_all(module_with_setup)
    assert len(results) == 2
    for dep, ok, msg in results:
        assert isinstance(dep, SetupDependency)
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
