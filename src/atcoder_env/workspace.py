"""Create editable problem workspaces without replacing existing files."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

TEMPLATES = ("array", "tree")


def create_workspace(path: Path, *, url: str | None = None, template: str = "array") -> Path:
    """Copy a bundled template into a new or empty directory.

    Templates describe example input shapes, not the constraints of an arbitrary
    AtCoder problem. The caller must adjust cases.py and implement both solutions.
    """
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template {template!r}; choose one of {TEMPLATES}.")
    destination = Path(path).expanduser().absolute()
    if destination.is_symlink():
        raise FileExistsError(f"Refusing a symbolic-link destination: {destination}")
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise FileExistsError(f"Workspace must be a new or empty directory: {destination}")

    source = resources.files("atcoder_env").joinpath("templates", template)
    # Read all resources before creating the destination, so a missing package
    # resource does not leave an apparently initialized workspace behind.
    files: dict[str, str] = {}
    for item in source.iterdir():
        if item.is_file() and item.name.endswith(".tpl"):
            content = item.read_text(encoding="utf-8")
            content = content.replace("__PROBLEM_URL__", json.dumps(url or "", ensure_ascii=False))
            files[item.name.removesuffix(".tpl")] = content
    if not files:
        raise RuntimeError(f"No packaged files found for template {template!r}.")

    destination.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(files.items()):
        # Exclusive creation also avoids overwrites if another process writes a
        # file after the initial directory check.
        with (destination / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    (destination / "test").mkdir(exist_ok=True)
    return destination
