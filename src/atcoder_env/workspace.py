"""Create editable problem workspaces without replacing existing files."""

from __future__ import annotations

import ast
import json
import tomllib
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

from atcoder_env.external import task_parts

TEMPLATES = ("array", "tree")


def require_configured_generator(filename: Path) -> None:
    """Reject unchanged example generators, including existing legacy copies.

    Templates are input-shape examples, not verified constraints for a problem.
    Compare syntax before importing user code so formatting, comments, or a
    different module description cannot make an unchanged scaffold look ready.
    """
    if not filename.is_file():
        raise ValueError(f"Missing {filename}; create strategy() and serialize(case) first")

    def canonical_source(source: str) -> str:
        tree = ast.parse(source)
        if ast.get_docstring(tree) is not None:
            tree.body.pop(0)
        return ast.dump(tree, include_attributes=False)

    try:
        current = canonical_source(filename.read_text(encoding="utf-8"))
    except SyntaxError:
        # Leave syntax diagnostics to the normal module import below.
        return
    for template in TEMPLATES:
        source = resources.files("atcoder_env").joinpath("templates", template, "cases.py.tpl")
        if current == canonical_source(source.read_text(encoding="utf-8")):
            raise ValueError(
                f"{filename} is the unchanged {template} example generator. "
                "Run ac constraints <problem> to import supported constraints, or edit "
                "cases.py to match this problem's input and constraints before stress/bench."
            )


def resolve_workspace(target: str, *, directory: Path | None = None) -> tuple[Path, str | None]:
    """Resolve a local path or task URL using the same layout as ac prepare."""
    if urlparse(target).scheme in {"http", "https"} or "://" in target:
        contest, task = task_parts(target)
        url = f"https://atcoder.jp/contests/{contest}/tasks/{task}"
        if directory is not None:
            return directory.expanduser(), url
        current = Path.cwd()
        if (current / "atcoder.toml").is_file():
            try:
                data = tomllib.loads((current / "atcoder.toml").read_text(encoding="utf-8"))
                problem = data.get("problem", {})
                current_url = problem.get("url") if isinstance(problem, dict) else None
                if isinstance(current_url, str) and task_parts(current_url) == (contest, task):
                    return current, url
            except (OSError, ValueError):
                # This is only a location hint. The selected workspace's full
                # configuration is validated later by the command dispatcher.
                pass
        return Path("problems") / contest / task, url
    if directory is not None:
        raise ValueError("Use --directory with a task URL; pass a local path directly otherwise")
    return Path(target).expanduser(), None


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
