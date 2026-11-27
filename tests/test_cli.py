"""Verify CLI routing, exit codes, and safe workspace/file behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

URL = "https://atcoder.jp/contests/abc001/tasks/abc001_1"


def invoke(cwd: Path, *arguments: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [sys.executable, "-m", "atcoder_env", *arguments],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def fake_external_tools(directory: Path) -> dict[str, str]:
    """Replace network adapters with deterministic executable fixtures."""
    bindir = directory / "fake-bin"
    bindir.mkdir()
    sources = {
        "oj": (
            "import pathlib, sys\n"
            "assert sys.argv[1] == 'download', sys.argv\n"
            "folder = pathlib.Path(sys.argv[sys.argv.index('--directory') + 1])\n"
            "folder.mkdir(parents=True)\n"
            "(folder / 'sample-1.in').write_text('1 2\\n')\n"
            "(folder / 'sample-1.out').write_text('3\\n')\n"
            "print('fixture sample downloaded')\n"
        ),
        "oj-template": (
            "import sys\n"
            "assert sys.argv[1] == '-t', sys.argv\n"
            "print('# generated draft for ' + sys.argv[2])\n"
            "print('print(123)')\n"
        ),
    }
    for name, source in sources.items():
        path = bindir / name
        path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        path.chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}


@pytest.mark.parametrize("target_argument", [".", "problem"])
def test_new_accepts_existing_empty_directory(tmp_path: Path, target_argument: str) -> None:
    destination = tmp_path / target_argument
    destination.mkdir(exist_ok=True)
    result = invoke(tmp_path, "new", target_argument)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / "main.py").is_file()
    assert (destination / "cases.py").is_file()
    assert (destination / "test").is_dir()


def test_new_scaffold_cannot_silently_pass_stress(tmp_path: Path) -> None:
    created = invoke(tmp_path, "new", "problem")
    assert created.returncode == 0, created.stdout + created.stderr
    result = invoke(tmp_path, "stress", "problem", "--max-examples", "1")
    assert result.returncode == 2
    assert "unchanged array example generator" in result.stdout
    assert "ac constraints" in result.stdout
    assert not (tmp_path / "problem" / ".ac" / "runs").exists()


def test_new_never_overwrites_existing_solution(tmp_path: Path) -> None:
    destination = tmp_path / "problem"
    destination.mkdir()
    source = destination / "main.py"
    source.write_text("print('my solution')\n")
    result = invoke(tmp_path, "new", "problem")
    assert result.returncode == 2
    assert source.read_text() == "print('my solution')\n"
    assert not (destination / "brute.py").exists()


def test_prepare_existing_empty_cwd_downloads_only_samples(tmp_path: Path) -> None:
    env = fake_external_tools(tmp_path)
    destination = tmp_path / "workspace"
    destination.mkdir()
    result = invoke(destination, "prepare", URL, "--directory", ".", "--no-constraints", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / "test" / "sample-1.in").read_text() == "1 2\n"
    assert (destination / "test" / "sample-1.out").read_text() == "3\n"
    assert "NotImplementedError" in (destination / "main.py").read_text()


def test_invalid_url_is_rejected_without_creating_target(tmp_path: Path) -> None:
    result = invoke(tmp_path, "prepare", "https://example.com/problem", "--directory", "problem")
    assert result.returncode == 2
    assert "atcoder.jp/contests" in result.stdout
    assert not (tmp_path / "problem").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ("stress", "--budget", "nan"),
        ("stress", "--max-examples", "0"),
        ("test", "--timeout", "-1"),
        ("test", "--timeout", "inf"),
        ("bench", "--sizes", "1,1"),
        ("bench", "--sizes", "0,1"),
        ("bench", "--repeat", "0"),
        ("bench", "--memory-limit", "0"),
        ("bench", "--slow-seconds", "nan"),
        ("bench", "--shrink-budget", "0"),
        ("bench", "--profile-timeout", "inf"),
    ],
)
def test_invalid_options_exit_two_before_execution(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    result = invoke(tmp_path, *arguments)
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert not (tmp_path / ".ac").exists()


def test_samples_save_actual_stderr_and_dual_logs(tmp_path: Path) -> None:
    assert invoke(tmp_path, "new", "problem").returncode == 0
    root = tmp_path / "problem"
    (root / "main.py").write_text(
        "import sys\n"
        "print(sum(map(int, sys.stdin.read().split())))\n"
        "print('debug marker', file=sys.stderr)\n"
    )
    (root / "test" / "sample.in").write_text("1 2\n")
    (root / "test" / "sample.out").write_text("3\n")
    result = invoke(tmp_path, "test", "problem")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sample: AC" in result.stdout
    assert "sample: AC" in (root / ".ac" / "logs" / "ac.log").read_text()
    session = next((root / ".ac" / "runs").glob("*-test-*"))
    saved = session / "001-sample"
    assert (saved / "actual.txt").read_text() == "3\n"
    assert (saved / "stderr.txt").read_text() == "debug marker\n"
    record = json.loads((saved / "result.json").read_text())
    assert record["verdict"] == "AC"
    assert record["solution"]["stderr"] == "debug marker\n"

    (root / "main.py").write_text("raise RuntimeError('user bug')\n")
    failure = invoke(tmp_path, "test", "problem")
    assert failure.returncode == 1
    assert "sample: RE" in failure.stdout
    assert "RuntimeError: user bug" in failure.stdout


def test_template_generates_drafts_without_overwriting_source(tmp_path: Path) -> None:
    env = fake_external_tools(tmp_path)
    created = invoke(
        tmp_path, "prepare", URL, "--directory", "problem", "--no-constraints", env=env
    )
    assert created.returncode == 0, created.stdout + created.stderr
    root = tmp_path / "problem"
    source = root / "main.py"
    source.write_text("print('user solution')\n")
    result = invoke(tmp_path, "template", "problem", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert source.read_text() == "print('user solution')\n"
    folder = next((root / ".ac" / "templates").iterdir())
    for name in ("main.py", "generate.py"):
        draft = (folder / name).read_text()
        assert "generated draft for " + name in draft
        assert "https://github.com/kitamura-tetsuo/atcoder-environment" in draft


def test_download_conflict_preserves_existing_samples(tmp_path: Path) -> None:
    env = fake_external_tools(tmp_path)
    created = invoke(
        tmp_path, "prepare", URL, "--directory", "problem", "--no-constraints", env=env
    )
    assert created.returncode == 0, created.stdout + created.stderr
    root = tmp_path / "problem"
    sample = root / "test" / "sample-1.in"
    sample.write_text("user case\n")
    result = invoke(tmp_path, "download", "problem", env=env)
    assert result.returncode == 2
    assert "Refusing to overwrite" in result.stdout
    assert sample.read_text() == "user case\n"
    assert (root / "test" / "sample-1.out").read_text() == "3\n"


@pytest.mark.parametrize("search_options", [("--no-shrink",), ("--budget", "0.5")])
def test_failure_must_reproduce_after_early_search_exit(
    tmp_path: Path, search_options: tuple[str, ...]
) -> None:
    root = tmp_path / "problem"
    assert invoke(tmp_path, "new", "problem").returncode == 0
    (root / "main.py").write_text(
        "import pathlib, time\n"
        "marker = pathlib.Path('seen-once')\n"
        "seen = marker.exists()\n"
        "marker.write_text('yes')\n"
        "if not seen: time.sleep(0.6)\n"
        "print(0 if seen else 1)\n"
    )
    (root / "brute.py").write_text("print(0)\n")
    (root / "cases.py").write_text(
        "from hypothesis import strategies as st\n"
        "def strategy(): return st.just(0)\n"
        "def serialize(case): return str(case) + '\\n'\n"
        "def edge_cases(): return [0]\n"
    )
    result = invoke(tmp_path, "stress", "problem", "--max-examples", "1", *search_options)
    assert result.returncode == 2, result.stdout + result.stderr
    session = next((root / ".ac" / "runs").glob("*-stress-*"))
    summary = json.loads((session / "summary.json").read_text())
    assert summary["status"] == "ERROR"
    assert (session / "initial" / "actual.txt").read_text() == "1\n"
    assert not (session / "failure").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="Root bypasses filesystem mode permissions")
def test_setup_error_does_not_require_writable_parent(tmp_path: Path) -> None:
    parent = tmp_path / "read-only-parent"
    current = parent / "workspace"
    current.mkdir(parents=True)
    parent.chmod(0o500)
    try:
        result = invoke(current, "prepare", "https://example.com/invalid")
    finally:
        parent.chmod(0o700)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "atcoder.jp/contests" in result.stdout
    assert "Traceback" not in result.stderr


def test_run_memory_limit_without_expected_output_reports_mle(tmp_path: Path) -> None:
    assert invoke(tmp_path, "new", "problem").returncode == 0
    root = tmp_path / "problem"
    (root / "main.py").write_text(
        "import time\nblocks = []\n"
        "while True:\n"
        "    blocks.append(bytearray(8 * 1024 * 1024))\n"
        "    time.sleep(0.01)\n"
    )
    data = tmp_path / "input.txt"
    data.write_text("1\n")
    result = invoke(tmp_path, "run", "problem", "--input", str(data), "--memory-limit", "64")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "MLE" in result.stdout
    assert "KeyError" not in result.stdout
    session = next((root / ".ac" / "runs").glob("*-run-*"))
    record = json.loads((session / "case" / "result.json").read_text())
    assert record["verdict"] == "MLE"
    assert record["solution"]["status"] == "memory_limit"
    assert record["solution"]["max_rss_kib"] > 64 * 1024
