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
    assert config["name"] == "AtCoder: Debug latest failure or tests"
    assert config["type"] == "debugpy"
    assert config["subProcess"] is True
    assert config["program"] == "${workspaceFolder}/.vscode/debug_test.py"
    assert config["args"] == ["${fileDirname}"]
    assert config["cwd"] == "${fileDirname}"
    assert "python" not in config


def make_problem(tmp_path, *, solution="print(0)\n", oracle="print(1)\n"):
    from atcoder_env.config import load_config
    from atcoder_env.workspace import create_workspace

    root = create_workspace(tmp_path / "problem with spaces")
    (root / "main.py").write_text(solution, encoding="utf-8")
    (root / "brute.py").write_text(oracle, encoding="utf-8")
    (root / "cases.py").write_text(
        "from hypothesis import strategies as st\n"
        "def strategy(): return st.just(9)\n"
        "def edge_cases(): return [9]\n"
        "def serialize(case): return str(case) + '\\nABCABCABC\\n'\n"
        "def benchmark_case(size, seed): return size\n",
        encoding="utf-8",
    )
    return load_config(root)


@pytest.mark.parametrize("read", ["sys.stdin.buffer.read()", "os.read(0, 8192)"])
def test_real_stress_failure_debugs_current_code_with_file_stdin(
    launcher, tmp_path, monkeypatch, read
):
    from atcoder_env.harness import stress

    config = make_problem(tmp_path)
    assert stress(config, max_examples=1, seed=0, shrink=False, budget=5) == 1
    runs = set((config.root / ".ac/runs").iterdir())
    failure = next(iter(runs)) / "failure/input.txt"
    saved = failure.read_bytes()
    # The archived buggy source must not be executed after the user edits it.
    (config.root / "helper.py").write_text("SUFFIX = b'current source'\n", encoding="utf-8")
    (config.root / "main.py").write_text(
        "import os, sys\nfrom pathlib import Path\nfrom helper import SUFFIX\n"
        f"data = {read}\n"
        "Path('debugged.bin').write_bytes(data + SUFFIX)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "oj_main", lambda _: pytest.fail("Use the saved failure"))
    launcher.main([str(config.root)])
    assert (config.root / "debugged.bin").read_bytes() == saved + b"current source"
    assert set((config.root / ".ac/runs").iterdir()) == runs
    assert failure.read_bytes() == saved
    log = (config.root / ".ac/logs/ac.log").read_text()
    assert "stress WA" in log
    assert str(failure) in log


def test_real_benchmark_runtime_error_debugs_its_saved_input(launcher, tmp_path, monkeypatch):
    from atcoder_env.benchmark import benchmark

    config = make_problem(tmp_path, solution="raise RuntimeError('original bug')\n")
    assert (
        benchmark(
            config, sizes=[9], repeat=1, seed=0, pattern="default", shrink=False, profile=False
        )
        == 1
    )
    session = next((config.root / ".ac/runs").iterdir())
    saved = (session / "size-9-run-1/input.txt").read_bytes()
    assert not list(session.glob("*/failure/input.txt"))
    (config.root / "main.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path('bench-debugged.bin').write_bytes(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "oj_main", lambda _: pytest.fail("Debug the failed benchmark"))
    launcher.main([str(config.root)])
    assert (config.root / "bench-debugged.bin").read_bytes() == saved


def test_real_oracle_failure_debugs_current_oracle(launcher, tmp_path, monkeypatch):
    from atcoder_env.harness import OracleFailure, stress

    config = make_problem(tmp_path, oracle="raise RuntimeError('reference bug')\n")
    with pytest.raises(OracleFailure):
        stress(config, max_examples=1, seed=0, budget=5)
    session = next((config.root / ".ac/runs").iterdir())
    saved = (session / "oracle-error/input.txt").read_bytes()
    (config.root / "main.py").write_text(
        "from pathlib import Path\nPath('wrong-program').touch()\n", encoding="utf-8"
    )
    (config.root / "brute.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path('oracle-debugged.bin').write_bytes(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "oj_main", lambda _: pytest.fail("Debug the reference failure"))
    launcher.main([str(config.root)])
    assert (config.root / "oracle-debugged.bin").read_bytes() == saved
    assert not (config.root / "wrong-program").exists()


def test_new_passing_run_falls_back_to_samples_after_an_older_failure(
    launcher, tmp_path, monkeypatch
):
    from atcoder_env.harness import stress

    config = make_problem(tmp_path)
    assert stress(config, max_examples=1, seed=0, shrink=False, budget=5) == 1
    (config.root / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert stress(config, max_examples=1, seed=0, budget=5) == 0
    monkeypatch.chdir(tmp_path)
    called = []
    monkeypatch.setattr(launcher, "oj_main", lambda args: called.append(args))
    launcher.main([str(config.root)])
    assert called == [["t", "-c", f"{shlex.quote(sys.executable)} main.py", "-d", "test"]]
    assert "No saved failure in the latest ac run" in (config.root / ".ac/logs/ac.log").read_text()


def test_failed_patterns_use_current_configured_command_in_order(launcher, tmp_path, monkeypatch):
    from atcoder_env.debug_artifacts import FailureTarget

    root = tmp_path / "with spaces"
    root.mkdir()
    program = root / "custom solution.py"
    program.write_text(
        "import sys\nfrom pathlib import Path\n"
        "with Path(sys.argv[1]).open('a') as stream:\n"
        "    stream.write(sys.stdin.read())\n",
        encoding="utf-8",
    )
    (root / "atcoder.toml").write_text(
        '[run]\nsolution = ["python", "custom solution.py", "order.txt"]\n', encoding="utf-8"
    )
    targets = []
    for index, pattern in enumerate(["palette", "random"], 1):
        data = root / f"input-{index}.txt"
        data.write_text(f"{index}\n", encoding="utf-8")
        targets.append(
            FailureTarget(
                input_path=data,
                session=root,
                kind="bench",
                verdict="TLE",
                pattern=pattern,
                size=index,
            )
        )
    monkeypatch.setattr(launcher, "find_latest_failures", lambda _: targets)
    monkeypatch.setattr(launcher, "oj_main", lambda _: pytest.fail("Use all failed patterns"))
    launcher.main([str(root)])
    assert (root / "order.txt").read_text() == "1\n2\n"
