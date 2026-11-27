"""Exercise automatic benchmark sizes through the CLI and persisted measurements."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

URL = "https://atcoder.jp/contests/abc485/tasks/abc485_c"
STATEMENT = Path(__file__).parent / "fixtures" / "abc485_c_constraints.html"


def invoke(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "atcoder_env", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def generated_problem(tmp_path: Path) -> Path:
    result = invoke(tmp_path, "constraints", URL, "--html", str(STATEMENT.resolve()))
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / "problems" / "abc485" / "abc485_c"
    (root / "main.py").write_text(
        "import sys\n"
        "tokens = sys.stdin.buffer.read().split()\n"
        "size = int(tokens[0])\n"
        "assert len(tokens) == size + 1\n"
        "print(size)\n",
        encoding="utf-8",
    )
    return root


def measurements(root: Path) -> tuple[Path, list[dict[str, str]]]:
    sessions = list((root / ".ac" / "runs").glob("*-bench-*"))
    assert len(sessions) == 1
    with (sessions[0] / "benchmark.csv").open(encoding="utf-8", newline="") as stream:
        return sessions[0], list(csv.DictReader(stream))


def forbid_execution(root: Path) -> None:
    marker = root / "executed"
    for filename in ("main.py", "cases.py"):
        (root / filename).write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('unexpected')\n"
            "raise AssertionError('The benchmark should fail before running user code')\n",
            encoding="utf-8",
        )


def test_url_benchmark_defaults_execute_official_maximum_and_save_evidence(
    tmp_path: Path, generated_problem: Path
):
    result = invoke(tmp_path, "bench", URL, "--repeat", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    session, rows = measurements(generated_problem)
    assert [int(row["size"]) for row in rows if row["pattern"] == "default"] == [
        2,
        10,
        100,
        1_000,
        10_000,
        100_000,
        300_000,
    ]
    assert all(row["status"] == "ok" for row in rows)
    assert all(row["runs"] == ("0" if row["duplicate_of"] else "1") for row in rows)
    assert {row["pattern"] for row in rows} == {
        "default",
        "balanced",
        "alternating",
        "blocks",
        "palette",
        "distinct",
        "sorted",
        "reversed",
        "random",
    }
    maximum = session / "size-300000-run-1"
    assert (maximum / "actual.txt").read_text() == "300000\n"
    assert len((maximum / "input.txt").read_text().split()) == 300_001
    summary = json.loads((session / "summary.json").read_text())
    assert summary["checks_correctness"] is False
    assert summary["rows"][-1]["size"] == 300_000
    assert "n=300000, pattern=" in result.stdout
    assert "n=300000, pattern=" in (generated_problem / ".ac" / "logs" / "ac.log").read_text()


def test_explicit_sizes_override_defaults_and_preserve_requested_order(
    tmp_path: Path, generated_problem: Path
):
    result = invoke(
        tmp_path, "bench", str(generated_problem), "--sizes", "1000,2,100", "--repeat", "1"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _session, rows = measurements(generated_problem)
    assert [int(row["size"]) for row in rows if row["pattern"] == "default"] == [1_000, 2, 100]


def test_out_of_range_size_fails_before_any_generator_solution_or_session(
    tmp_path: Path, generated_problem: Path
):
    forbid_execution(generated_problem)
    result = invoke(tmp_path, "bench", URL, "--sizes", "2,300001", "--repeat", "1")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "outside the official N range 2..300000" in result.stdout
    assert not (generated_problem / "executed").exists()
    assert not (generated_problem / ".ac" / "runs").exists()


def test_missing_constraints_support_custom_generator_with_legacy_defaults(
    tmp_path: Path, generated_problem: Path
):
    (generated_problem / "constraints.toml").unlink()
    (generated_problem / "cases.py").write_text(
        "from hypothesis import strategies as st\n"
        "def strategy(): return st.just(1)\n"
        "def serialize(size): return str(size) + '\\n' + '0 ' * size + '\\n'\n"
        "def benchmark_case(size, seed): return size\n",
        encoding="utf-8",
    )
    result = invoke(tmp_path, "bench", URL, "--repeat", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    _session, rows = measurements(generated_problem)
    assert [int(row["size"]) for row in rows] == [100, 1_000, 10_000]
    assert all(row["status"] == "ok" for row in rows)


def test_malformed_constraints_fail_before_any_generator_solution_or_session(
    tmp_path: Path, generated_problem: Path
):
    (generated_problem / "constraints.toml").write_text("[size\n", encoding="utf-8")
    forbid_execution(generated_problem)
    result = invoke(tmp_path, "bench", URL, "--repeat", "1")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "constraints.toml for benchmark sizes" in result.stdout
    assert not (generated_problem / "executed").exists()
    assert not (generated_problem / ".ac" / "runs").exists()
