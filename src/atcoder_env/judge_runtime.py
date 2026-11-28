"""Verify the official CPython package pins in the local environment."""

from __future__ import annotations

import importlib.metadata
import json
import platform
from importlib.resources import files

from loguru import logger


def judge_manifest() -> dict:
    return json.loads(files("atcoder_env").joinpath("judge_versions.json").read_text())


def judge_runtime_status() -> dict:
    manifest = judge_manifest()
    problems = []
    actual_python = platform.python_version()
    if platform.python_implementation() != "CPython" or actual_python != manifest["python"]:
        problems.append(
            f"Python: expected CPython {manifest['python']}, "
            f"got {platform.python_implementation()} {actual_python}"
        )
    versions = {}
    for name, expected in manifest["libraries"].items():
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
            problems.append(f"{name}: missing")
            continue
        actual = distribution.version
        if isinstance(expected, dict):
            direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
            commit = direct_url.get("vcs_info", {}).get("commit_id")
            versions[name] = {"version": actual, "commit": commit}
            if commit != expected["commit"]:
                problems.append(f"{name}: expected Git commit {expected['commit']}, got {commit}")
        else:
            versions[name] = actual
            if actual != expected:
                problems.append(f"{name}: expected {expected}, got {actual}")
    return {
        "source": manifest["source"],
        "expected_python": manifest["python"],
        "python": actual_python,
        "libraries": versions,
        "mismatches": problems,
    }


def check_judge_runtime() -> None:
    status = judge_runtime_status()
    if status["mismatches"]:
        for problem in status["mismatches"]:
            logger.error("Judge environment: {}", problem)
        raise ValueError(
            "AtCoder library versions differ; rebuild the container or run uv sync --frozen"
        )
    logger.info(
        "AtCoder CPython {}: all {} official libraries match the pinned versions",
        status["python"],
        len(status["libraries"]),
    )
