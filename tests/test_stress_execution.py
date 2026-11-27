"""Check differential-search behavior across execution modes with real programs."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from atcoder_env.config import Config, load_config
from atcoder_env.harness import stress

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "pair-sum"


def test_parallel_and_sequential_search_save_the_same_seeded_counterexample(tmp_path):
    failures = []
    for parallel in (False, True):
        root = tmp_path / str(parallel)
        shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(".ac", "__pycache__"))
        config = load_config(root)
        config.solution = [sys.executable, "buggy.py"]
        assert stress(config, max_examples=30, seed=0, budget=30, parallel=parallel) == 1
        session = next((root / ".ac" / "runs").glob("*-stress-*"))
        summary = json.loads((session / "summary.json").read_text())
        assert summary["status"] == "FAIL"
        assert summary["search_state"] == "reduced"
        assert summary["execution"]["mode"] == ("parallel" if parallel else "sequential")
        assert summary["execution"]["solution_seconds"] > 0
        assert summary["execution"]["oracle_seconds"] > 0
        assert summary["execution"]["serial_retries"] == 0
        assert summary["evaluations_per_second"] > 0
        failures.append(
            tuple(
                (session / stage / filename).read_text()
                for stage in ("initial", "failure")
                for filename in ("input.txt", "expected.txt", "actual.txt")
            )
        )
    assert failures[0] == failures[1]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("raise RuntimeError('solution bug')", "RE"),
        ("import time; time.sleep(20)", "TLE"),
        ("print('x' * 100000)", "OLE"),
    ],
)
def test_parallel_failures_survive_serial_reproduction(tmp_path, source, expected):
    (tmp_path / "cases.py").write_text(
        "from hypothesis import strategies as st\n"
        "def strategy(): return st.just(0)\n"
        "def serialize(case): return str(case) + '\\n'\n"
        "def edge_cases(): return [0]\n"
    )
    config = Config(
        root=tmp_path,
        solution=[sys.executable, "-c", source],
        oracle=[sys.executable, "-c", "print(0)"],
        timeout=0.2 if expected == "TLE" else 2.0,
        output_limit=64 if expected == "OLE" else 4096,
    )
    assert stress(config, max_examples=1, seed=42, budget=10, shrink=False) == 1
    session = next((tmp_path / ".ac" / "runs").glob("*-stress-*"))
    summary = json.loads((session / "summary.json").read_text())
    record = json.loads((session / "failure" / "result.json").read_text())
    assert record["verdict"] == expected
    assert summary["status"] == "FAIL"
    assert summary["execution"]["serial_retries"] == (1 if expected == "TLE" else 0)
