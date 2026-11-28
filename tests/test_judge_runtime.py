"""Keep the official manifest, resolver pins, and installed environment aligned."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from atcoder_env.judge_runtime import judge_manifest, judge_runtime_status

ROOT = Path(__file__).resolve().parents[1]


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_judge_pins_match_manifest_and_lock():
    manifest = judge_manifest()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    expected = manifest["libraries"]
    assert len(expected) == 43
    assert pyproject["project"]["requires-python"] == f"=={manifest['python']}"
    assert lock["requires-python"] == f"=={manifest['python']}"
    assert (ROOT / ".python-version").read_text().strip() == manifest["python"]
    assert f"FROM python:{manifest['python']}-" in (ROOT / "Dockerfile").read_text()
    deps = set(pyproject["dependency-groups"]["judge"])
    assert len(deps) == len(expected)
    packages = {package["name"]: package for package in lock["package"]}
    for name, version in expected.items():
        package = packages[normalize(name)]
        if isinstance(version, dict):
            assert name in deps
            assert pyproject["tool"]["uv"]["sources"][name]["rev"] == version["commit"]
            assert package["source"]["git"].endswith("#" + version["commit"])
        else:
            assert f"{name}=={version}" in deps
            assert package["version"] == version
    assert packages["torch"]["source"]["registry"] == "https://download.pytorch.org/whl/cpu"


def test_installed_judge_runtime_matches_pins():
    status = judge_runtime_status()
    assert status["mismatches"] == [], status
