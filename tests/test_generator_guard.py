"""Reject untouched example data before reporting problem-specific test results."""

from __future__ import annotations

import ast
import shutil
from importlib import resources
from pathlib import Path

import pytest

from atcoder_env import cli, constraints
from atcoder_env.harness import case_module
from atcoder_env.workspace import create_workspace, require_configured_generator

URL = "https://atcoder.jp/contests/abc485/tasks/abc485_d"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "pair-sum"


@pytest.mark.parametrize("template", ["array", "tree"])
@pytest.mark.parametrize("url", [None, URL])
def test_unmodified_templates_require_real_problem_configuration(
    tmp_path: Path, template: str, url: str | None
) -> None:
    root = create_workspace(tmp_path / "problem", template=template, url=url)
    original = (root / "cases.py").read_bytes()
    with pytest.raises(ValueError, match=f"unchanged {template} example generator"):
        with case_module(root):
            pytest.fail("An unchanged input example must not be loaded for execution")
    assert (root / "cases.py").read_bytes() == original


@pytest.mark.parametrize("template", ["array", "tree"])
def test_legacy_copy_with_cosmetic_changes_is_still_unconfigured(
    tmp_path: Path, template: str
) -> None:
    # Old workspaces have no readiness metadata. Compare the actual generator.
    source = resources.files("atcoder_env").joinpath("templates", template, "cases.py.tpl")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tree.body.pop(0)
    text = '"""My description."""\n# A formatting-only edit.\n' + ast.unparse(tree) + "\n"
    filename = tmp_path / "cases.py"
    filename.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    with pytest.raises(ValueError, match="unchanged .* example generator"):
        require_configured_generator(filename)


@pytest.mark.parametrize("action", ["bench", "stress"])
def test_unconfigured_workspaces_do_not_run_solutions_or_create_measurement_sessions(
    tmp_path: Path, action: str
) -> None:
    root = create_workspace(tmp_path / "problem", url=URL)
    for filename in ("main.py", "brute.py"):
        (root / filename).write_text(
            "from pathlib import Path\nPath('executed').touch()\nprint(0)\n",
            encoding="utf-8",
        )
    options = ["--sizes", "3", "--repeat", "1"] if action == "bench" else ["--max-examples", "1"]
    assert cli.main([action, str(root), *options]) == 2
    assert not (root / "executed").exists()
    assert not (root / ".ac" / "runs").exists()
    assert "unchanged array example generator" in (root / ".ac" / "logs" / "ac.log").read_text()


def test_unsupported_import_cannot_leave_a_runnable_numeric_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "problem"
    fixture = Path(__file__).parent / "fixtures" / "abc485_c_constraints.html"
    unsupported = fixture.read_bytes().replace(
        b"</ul>", b"<li>The number of distinct values must be even.</li></ul>"
    )
    monkeypatch.setattr(cli, "download_samples", lambda config: 0)
    monkeypatch.setattr(constraints, "fetch_statement", lambda url: unsupported)
    assert cli.main(["prepare", URL, "-d", str(root)]) == 2
    assert not (root / "constraints.toml").exists()
    assert cli.main(["bench", str(root), "--sizes", "3", "--repeat", "1"]) == 2
    assert cli.main(["stress", str(root), "--max-examples", "1"]) == 2
    assert not (root / ".ac" / "runs").exists()


def test_custom_generator_and_saved_samples_remain_usable(tmp_path: Path) -> None:
    root = create_workspace(tmp_path / "problem", url=URL)
    (root / "main.py").write_text("print(input())\n", encoding="utf-8")
    (root / "brute.py").write_text("print(input())\n", encoding="utf-8")
    (root / "test" / "sample.in").write_text("3\nABC\n", encoding="utf-8")
    (root / "test" / "sample.out").write_text("3\n", encoding="utf-8")
    # Sample testing does not generate inputs, so an untouched cases.py is fine.
    assert cli.main(["test", str(root)]) == 0
    (root / "cases.py").write_text(
        "from hypothesis import strategies as st\n"
        "def strategy(): return st.text(alphabet='ABC', min_size=3, max_size=5)\n"
        "def serialize(case): return str(len(case)) + '\\n' + case + '\\n'\n"
        "def benchmark_case(size, seed): return 'A' * size\n",
        encoding="utf-8",
    )
    assert cli.main(["stress", str(root), "--max-examples", "3"]) == 0
    assert cli.main(["bench", str(root), "--sizes", "3", "--repeat", "1"]) == 0


def test_bundled_working_example_is_not_mistaken_for_scaffold(tmp_path: Path) -> None:
    root = tmp_path / "problem"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(".ac", "__pycache__"))
    with case_module(root) as cases:
        assert cases.serialize(cases.benchmark_case(3, 0))
