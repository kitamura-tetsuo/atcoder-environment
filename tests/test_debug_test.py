"""Exercise the VS Code launcher without requiring the editor in CI."""

from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launcher():
    spec = importlib.util.spec_from_file_location("debug_test", ROOT / ".vscode/debug_test.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("interactive", [False, True])
def test_launcher_runs_oj_in_problem_directory(launcher, tmp_path, monkeypatch, interactive):
    problem = tmp_path / "problem with spaces"
    problem.mkdir()
    (problem / "main.py").write_text("print(input())\n")
    (problem / "test").mkdir()
    if interactive:
        (problem / "judge.py").write_text("")
    monkeypatch.chdir(tmp_path)
    original_check = launcher.oj_test.check_gnu_time
    called = []

    def fake_oj(args):
        assert Path.cwd() == problem
        assert launcher.oj_test.check_gnu_time("time") is False
        called.append(args)
        raise SystemExit(1)

    monkeypatch.setattr(launcher, "oj_main", fake_oj)
    launcher.main([str(problem)])
    python = shlex.quote(sys.executable)
    assert called == [
        ["t/r", "-c", f"{python} main.py", f"{python} judge.py"]
        if interactive
        else ["t", "-c", f"{python} main.py", "-d", "test"]
    ]
    assert launcher.oj_test.check_gnu_time is original_check


def test_launcher_runs_real_sample(launcher, tmp_path, monkeypatch, capfd):
    import onlinejudge_command.update_checking as update_checking

    (tmp_path / "main.py").write_text("print(int(input()) * 2)\n")
    tests = tmp_path / "test"
    tests.mkdir()
    (tests / "sample-1.in").write_text("21\n")
    (tests / "sample-1.out").write_text("42\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(update_checking, "run", lambda: True)
    # oj uses logging, so capture the actual exit status without modifying its judge.
    actual = launcher.oj_main
    exit_codes = []

    def run_oj(args):
        try:
            actual(args)
        except SystemExit as error:
            exit_codes.append(error.code)
            raise

    monkeypatch.setattr(launcher, "oj_main", run_oj)
    launcher.main([str(tmp_path)])
    assert exit_codes == [0]
    assert not capfd.readouterr().err


def test_launch_configuration_supports_subprocess_and_selected_interpreter():
    config = json.loads((ROOT / ".vscode/launch.json").read_text())["configurations"][0]
    assert config["type"] == "debugpy"
    assert config["subProcess"] is True
    assert config["program"] == "${workspaceFolder}/.vscode/debug_test.py"
    assert config["args"] == ["${fileDirname}"]
    assert config["cwd"] == "${fileDirname}"
    assert "python" not in config
