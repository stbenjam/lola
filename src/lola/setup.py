"""
Setup dependency checking and execution for lola modules.

Provides functions to check whether a module's declared setup dependencies
(tools, auth, env vars) are satisfied, and to run setup scripts interactively.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - required for running setup scripts
from pathlib import Path

from lola.models import Module, SetupDependency


def _is_script_path(value: str, content_path: Path) -> bool:
    """Check if a value refers to an existing file relative to content_path."""
    return (content_path / value).exists()


def _validate_script_path(script_path: Path, module_path: Path) -> None:
    """Ensure a script path is within the module directory."""
    try:
        script_path.resolve().relative_to(module_path.resolve())
    except ValueError:
        raise ValueError(
            f"script path outside module directory: {script_path}"
        )


def check_dependency(dep: SetupDependency, module: Module) -> tuple[bool, str]:
    """
    Run a setup dependency's check command.

    Returns (is_satisfied, output_or_error).

    The check field can be:
    - A script path relative to content_path (if the file exists)
    - An inline shell command (run via bash -c)
    """
    try:
        if _is_script_path(dep.check, module.content_path):
            full_path = (module.content_path / dep.check).resolve()
            _validate_script_path(full_path, module.path)
            cmd: list[str] = ["bash", str(full_path)]
        else:
            cmd = ["bash", "-c", dep.check]

        result = subprocess.run(  # nosec B603 B607
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output

    except subprocess.TimeoutExpired:
        return False, "check timed out"
    except FileNotFoundError:
        return False, "bash not found"


def run_setup(dep: SetupDependency, module: Module) -> bool:
    """
    Run a setup dependency's install command/script interactively.

    Returns True if the command exited successfully.

    stdin is passed through to support interactive flows
    (browser-based OAuth, password prompts, etc.).
    """
    if not dep.install:
        return False

    env = os.environ.copy()
    env.update(
        {
            "LOLA_MODULE_NAME": module.name,
            "LOLA_MODULE_PATH": str(module.path),
            "LOLA_SETUP_NAME": dep.name,
        }
    )

    try:
        if _is_script_path(dep.install, module.content_path):
            full_path = (module.content_path / dep.install).resolve()
            _validate_script_path(full_path, module.path)
            cmd: list[str] = ["bash", str(full_path)]
        else:
            cmd = ["bash", "-c", dep.install]

        result = subprocess.run(  # nosec B603 B607
            cmd,
            env=env,
            text=True,
            timeout=600,
        )
        return result.returncode == 0

    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        return False


def check_all(module: Module) -> list[tuple[SetupDependency, bool, str]]:
    """
    Check all setup dependencies for a module.

    Returns list of (dependency, is_satisfied, message) tuples.
    """
    return [
        (dep, *check_dependency(dep, module))
        for dep in module.setup
    ]
