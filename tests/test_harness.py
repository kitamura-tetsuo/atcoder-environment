"""Verify complete local-judge workflows using real solution subprocesses."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from atcoder_env.config import Config, load_config
from atcoder_env.harness import (
    OracleFailure,
    benchmark,
    promote,
    replay,
    run_input,
    stress,
)
from atcoder_env.harness import (
    test_samples as judge_samples,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "pair-sum"


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "pair-sum"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(".ac", "__pycache__"))
    return load_config(root)


def latest_session(config: Config, kind: str) -> Path:
    return sorted((config.root / ".ac" / "runs").glob(f"*-{kind}-*"))[-1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_correct_solution_passes_samples_and_differential_search(workspace):
    assert judge_samples(workspace) == 0
    sample_summary = read_json(latest_session(workspace, "test") / "summary.json")
    assert sample_summary["passed"] == 3
    assert sample_summary["failed"] == 0
    assert stress(workspace, max_examples=20, seed=19, budget=15) == 0
    session = latest_session(workspace, "stress")
    summary = read_json(session / "summary.json")
    assert summary["status"] == "PASS"
    assert summary["seed"] == 19
    assert summary["evaluations"] >= 20
    assert not (session / "failure").exists()


def test_bug_is_shrunk_saved_replayed_and_promoted(workspace):
    workspace.solution = [sys.executable, "buggy.py"]
    assert stress(workspace, max_examples=30, seed=0, budget=20) == 1
    session = latest_session(workspace, "stress")
    initial = session / "initial"
    failure = session / "failure"
    initial_input = (initial / "input.txt").read_text()
    reduced_input = (failure / "input.txt").read_text()
    initial_n = int(initial_input.split()[0])
    numbers = list(map(int, reduced_input.split()))
    n, _target = numbers[:2]
    assert n == len(numbers[2:])
    assert 1 <= n < initial_n

    # Verify the saved answer independently by invoking the reference program.
    expected = subprocess.run(
        workspace.oracle,
        cwd=workspace.root,
        input=reduced_input,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout
    assert (failure / "expected.txt").read_text() == expected
    assert (failure / "actual.txt").read_text() != expected
    for artifact in (initial, failure):
        for filename in (
            "input.txt",
            "expected.txt",
            "actual.txt",
            "stderr.txt",
            "oracle.stdout.txt",
            "oracle.stderr.txt",
            "result.json",
        ):
            assert (artifact / filename).is_file()
        result = read_json(artifact / "result.json")
        assert result["seed"] == 0
        assert result["verdict"] == "WA"
        assert result["solution"]["status"] == "ok"
        assert result["oracle"]["status"] == "ok"
    summary = read_json(session / "summary.json")
    assert summary["status"] == "FAIL"
    assert summary["seed"] == 0
    assert summary["search_state"] == "reduced"
    environment = read_json(session / "environment.json")
    assert "buggy.py" in environment["source_sha256"]
    assert (session / "sources" / "buggy.py").read_text() == (
        workspace.root / "buggy.py"
    ).read_text()

    assert replay(workspace, failure) == 1
    workspace.solution = [sys.executable, "main.py"]
    assert replay(workspace, failure) == 0

    # Only the promoted case remains, proving the normal sample workflow finds it.
    shutil.rmtree(workspace.root / "test")
    assert promote(workspace, failure, "regression-duplicates") == 0
    assert (workspace.root / "test" / "regression-duplicates.in").read_text() == reduced_input
    with pytest.raises(ValueError, match="already exists"):
        promote(workspace, failure, "regression-duplicates")
    assert (workspace.root / "test" / "regression-duplicates.out").read_text() == expected
    workspace.solution = [sys.executable, "buggy.py"]
    assert judge_samples(workspace) == 1
    regression_summary = read_json(latest_session(workspace, "test") / "summary.json")
    assert regression_summary["failed"] == 1
    assert regression_summary["cases"][0]["name"] == "regression-duplicates"


@pytest.mark.parametrize(
    ("oracle_code", "expected_status"),
    [
        ("raise RuntimeError('broken reference')", "ORACLE_RE"),
        ("import time; time.sleep(10)", "ORACLE_TLE"),
    ],
)
def test_invalid_oracle_is_error_instead_of_solution_wa(workspace, oracle_code, expected_status):
    workspace.oracle = [sys.executable, "-c", oracle_code]
    workspace.oracle_timeout = 0.1
    with pytest.raises(OracleFailure, match=expected_status):
        stress(workspace, max_examples=15, seed=11, budget=5)
    session = latest_session(workspace, "stress")
    summary = read_json(session / "summary.json")
    assert summary["status"] == "ERROR"
    assert not (session / "failure").exists()
    result = read_json(session / "oracle-error" / "result.json")
    assert result["verdict"] == expected_status
    assert result["stage"] == "oracle"
    assert result["seed"] == 11
    if expected_status == "ORACLE_RE":
        assert "broken reference" in (session / "oracle-error" / "stderr.txt").read_text()


def test_empty_sample_set_and_missing_expected_output_are_errors(tmp_path):
    config = Config(root=tmp_path)
    with pytest.raises(ValueError, match="No test/\\*.in"):
        judge_samples(config)
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "missing.in").write_text("1\n")
    with pytest.raises(ValueError, match="Expected output missing"):
        judge_samples(config)


def test_exhausted_time_budget_never_reports_pass(workspace):
    assert stress(workspace, max_examples=20, seed=8, budget=1e-12) == 2
    summary = read_json(latest_session(workspace, "stress") / "summary.json")
    assert summary["status"] == "INCOMPLETE"
    assert summary["search_state"] == "budget_exceeded"
    assert summary["seed"] == 8


def test_run_input_retains_output_and_runtime_stderr(workspace):
    workspace.solution = [
        sys.executable,
        "-c",
        "import sys; print('partial output'); print('diagnostic', file=sys.stderr); sys.exit(2)",
    ]
    assert run_input(workspace, workspace.root / "test" / "sample-1.in") == 1
    session = latest_session(workspace, "run")
    assert (session / "case" / "actual.txt").read_text() == "partial output\n"
    assert (session / "case" / "stderr.txt").read_text() == "diagnostic\n"
    assert read_json(session / "case" / "result.json")["verdict"] == "RE"


def test_benchmark_saves_scaling_measurements_without_claiming_correctness(workspace):
    assert benchmark(workspace, sizes=[10, 100], repeat=2, seed=3) == 0
    session = latest_session(workspace, "bench")
    summary = read_json(session / "summary.json")
    assert summary["checks_correctness"] is False
    assert summary["seed"] == 3
    with (session / "benchmark.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [int(row["size"]) for row in rows] == [10, 100]
    assert all(int(row["runs"]) == 2 for row in rows)
    assert all(float(row["median_seconds"]) > 0 for row in rows)
    assert all(row["status"] == "ok" for row in rows)
