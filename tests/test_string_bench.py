"""Verify real CLI runs preserve an official string input's shape and bounds."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

URL = "https://atcoder.jp/contests/abc485/tasks/abc485_d"
STATEMENT = Path(__file__).parent / "fixtures" / "abc485_d_constraints.html"
VALIDATOR = """import sys
lines = sys.stdin.read().splitlines()
assert len(lines) == 2, lines[:3]
n = int(lines[0])
s = lines[1]
assert 3 <= n <= 300000
assert len(s) == n
assert set(s) <= set("ABC")
print(s.count("A"))
"""


def invoke(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "atcoder_env", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


@pytest.fixture
def string_problem(tmp_path: Path) -> Path:
    result = invoke(
        tmp_path, "constraints", URL, "--html", str(STATEMENT.resolve()), "--max-size", "12"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / "problems" / "abc485" / "abc485_d"
    for name in ("main.py", "brute.py"):
        (root / name).write_text(VALIDATOR, encoding="utf-8")
    return root


def test_string_benchmark_uses_official_sizes_and_valid_strings_for_every_pattern(
    tmp_path: Path, string_problem: Path
) -> None:
    result = invoke(tmp_path, "bench", URL, "--repeat", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    session = next((string_problem / ".ac" / "runs").glob("*-bench-*"))
    summary = json.loads((session / "summary.json").read_text())
    assert summary["sizes"] == [3, 10, 100, 1000, 10000, 100000, 300000]
    assert summary["sizes_source"] == "constraints.toml"
    assert summary["checks_correctness"] is False
    with (session / "benchmark.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert all(row["status"] == "ok" for row in rows)
    assert {row["pattern"] for row in rows} >= {
        "default",
        "palette",
        "blocks",
        "random",
        "constant",
        "constant_max",
    }
    assert all(int(row["size"]) <= 3 for row in rows if row["pattern"] == "distinct")
    saved_inputs = list(session.glob("size-*/input.txt"))
    assert saved_inputs
    for saved in saved_inputs:
        lines = saved.read_text().splitlines()
        assert len(lines) == 2
        size = int(lines[0])
        assert 3 <= size <= 300000
        assert len(lines[1]) == size
        assert set(lines[1]) <= set("ABC")
        assert saved.with_name("actual.txt").read_text() == str(lines[1].count("A")) + "\n"
    largest = session / "size-300000-run-1" / "input.txt"
    assert len(largest.read_text().splitlines()[1]) == 300000
    assert "n=300000" in result.stdout
    assert "n=300000" in (string_problem / ".ac" / "logs" / "ac.log").read_text()


def test_string_constraints_regeneration_preserves_solutions(
    tmp_path: Path, string_problem: Path
) -> None:
    original = {name: (string_problem / name).read_bytes() for name in ("main.py", "brute.py")}
    result = invoke(tmp_path, "constraints", URL, "--html", str(STATEMENT.resolve()))
    assert result.returncode == 0, result.stdout + result.stderr
    for name, data in original.items():
        assert (string_problem / name).read_bytes() == data
    reports = list((string_problem / ".ac" / "constraints").glob("*/report.json"))
    assert len(reports) == 2
    for filename in reports:
        report = json.loads(filename.read_text())
        assert report["status"] == "INSTALLED"
        assert report["unsupported"] == []
        assert report["spec"]["alphabet"] == "ABC"
        assert report["spec"]["size_min"] == 3
        assert report["spec"]["size_max"] == 300000


def test_string_stress_uses_the_same_validated_generator(
    tmp_path: Path, string_problem: Path
) -> None:
    result = invoke(tmp_path, "stress", URL, "--max-examples", "10", "--budget", "20")
    assert result.returncode == 0, result.stdout + result.stderr
    session = next((string_problem / ".ac" / "runs").glob("*-stress-*"))
    summary = json.loads((session / "summary.json").read_text())
    assert summary["evaluations"] >= 10
    assert not (session / "failure").exists()


@pytest.mark.parametrize("size", [2, 300_001, 3_000_000])
def test_string_out_of_range_manual_benchmark_runs_and_saves_input(
    tmp_path: Path, string_problem: Path, size: int
) -> None:
    (string_problem / "main.py").write_text(
        "import sys\n"
        "n, s = sys.stdin.read().splitlines()\n"
        f"assert int(n) == {size} and len(s) == int(n)\n"
        "assert set(s) <= set('ABC')\n"
        "print(n)\n",
        encoding="utf-8",
    )
    result = invoke(
        tmp_path,
        "bench",
        URL,
        "--sizes",
        str(size),
        "--pattern",
        "default",
        "--repeat",
        "1",
        "--no-profile",
        "--no-shrink",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Benchmark size {size} is outside the official N range 3..300000" in result.stdout
    session = next((string_problem / ".ac" / "runs").glob("*-bench-*"))
    summary = json.loads((session / "summary.json").read_text())
    assert summary["sizes"] == [size]
    assert summary["sizes_source"] == "--sizes"
    input_text = (session / f"size-{size}-run-1" / "input.txt").read_text()
    length, string = input_text.splitlines()
    assert int(length) == size == len(string)
    assert set(string) <= set("ABC")
    assert (session / f"size-{size}-run-1" / "actual.txt").read_text() == f"{size}\n"
