"""Exercise default distribution coverage and automatic resource diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from loguru import logger

from atcoder_env.benchmark import benchmark
from atcoder_env.config import Config
from atcoder_env.constraint_model import IntegerArrayConstraints


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def latest_benchmark(config: Config):
    session = sorted((config.root / ".ac" / "runs").glob("*-bench-*"))[-1]
    return session, read_json(session / "summary.json")


def array_workspace(tmp_path: Path, source: str, *, maximum: int = 12, distinct: int = 2):
    model = IntegerArrayConstraints("N", 2, maximum, "A", 1, "N", distinct)
    (tmp_path / "constraints.toml").write_text(model.to_toml(), encoding="utf-8")
    # This is the old generated interface: new hooks must be found on the bound model.
    (tmp_path / "cases.py").write_text(
        "from pathlib import Path\n"
        "from atcoder_env.constraint_model import IntegerArrayConstraints\n"
        "model = IntegerArrayConstraints.from_toml(Path(__file__).with_name('constraints.toml'))\n"
        "strategy = model.strategy\n"
        "serialize = model.serialize\n"
        "benchmark_case = model.benchmark_case\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(source, encoding="utf-8")
    (tmp_path / "brute.py").write_text(
        "raise RuntimeError('Oracle must not run')\n", encoding="utf-8"
    )
    return Config(root=tmp_path, timeout=1.0), model


def test_default_suite_discovers_existing_model_hooks_and_deduplicates(tmp_path: Path) -> None:
    config, model = array_workspace(tmp_path, "print('done')\n", distinct=1)
    assert benchmark(config, sizes=[2], repeat=1, seed=0) == 0
    session, summary = latest_benchmark(config)
    rows = summary["rows"]
    assert summary["pattern"] == "all"
    assert summary["coverage"] == "declared_patterns"
    assert summary["profile"] == "auto"
    assert summary["slow_seconds"] == 0.8 * config.timeout
    assert {row["pattern"] for row in rows} == set(model.benchmark_patterns(2))
    assert len(rows) > sum(row["runs"] for row in rows)
    assert sum(row["runs"] for row in rows) == len(list(session.glob("size-2*-run-1")))
    assert all(row["status"] == "ok" and row["max_rss_kib"] > 0 for row in rows)
    for row in rows:
        if row["duplicate_of"]:
            original = next(item for item in rows if item["pattern"] == row["duplicate_of"])
            assert row["runs"] == 0
            assert row["median_seconds"] == original["median_seconds"]
            assert row["max_rss_kib"] == original["max_rss_kib"]
    assert summary["diagnostics"] == []
    assert not list(session.rglob("line-profile.csv"))


def test_slow_input_is_profiled_automatically_but_fast_inputs_are_not(tmp_path: Path) -> None:
    source = (
        "import time\n"
        "n = int(input())\n"
        "values = list(map(int, input().split()))\n"
        "if values == [1] * n:\n"
        "    time.sleep(0.85)\n"
        "print(n)\n"
    )
    config, _ = array_workspace(tmp_path, source, distinct=1)
    assert benchmark(config, sizes=[2], repeat=1, seed=0, shrink=False) == 0
    session, summary = latest_benchmark(config)
    (diagnostic,) = summary["diagnostics"]
    assert diagnostic["pattern"] == "constant"
    assert diagnostic["resource_reason"] == "slow"
    assert diagnostic["profile_status"] == "ok"
    profile_path = Path(diagnostic["profile_path"])
    assert profile_path.is_file()
    assert "time.sleep(0.85)" in profile_path.read_text(encoding="utf-8")
    assert len(list(session.rglob("line-profile.csv"))) == 1
    for row in summary["rows"]:
        if row["pattern"] != "constant":
            assert row["profile_status"] == "not_needed"
    assert (config.root / "main.py").read_text(encoding="utf-8") == source


def test_one_timed_out_distribution_does_not_stop_other_families(tmp_path: Path) -> None:
    source = (
        "import time\n"
        "n = int(input())\n"
        "values = list(map(int, input().split()))\n"
        "transitions = sum(a != b for a, b in zip(values, values[1:]))\n"
        "if len(set(values)) == 2 and transitions == 1 and values.count(values[0]) == n // 2:\n"
        "    time.sleep(0.3)\n"
        "print(n)\n"
    )
    config, _ = array_workspace(tmp_path, source)
    config.timeout = 0.12
    assert benchmark(config, sizes=[4, 8], repeat=1, seed=0, shrink=False) == 1
    session, summary = latest_benchmark(config)
    rows = {(row["size"], row["pattern"]): row for row in summary["rows"]}
    assert rows[4, "balanced"]["status"] == "timeout"
    assert (8, "balanced") not in rows
    assert rows[8, "default"]["status"] == "ok"
    assert rows[8, "distinct"]["status"] == "ok"
    assert any(
        row == {"size": 8, "pattern": "balanced", "reason": "earlier_failure"}
        for row in summary["skipped"]
    )
    assert summary["completed"] is True
    balanced = next(item for item in summary["diagnostics"] if item["pattern"] == "balanced")
    assert balanced["resource_reason"] == "timeout"
    assert balanced["profile_status"] == "ok"
    assert (session / balanced["directory"] / "initial" / "input.txt").is_file()
    assert Path(balanced["profile_path"]).is_file()


def test_memory_failure_is_reduced_without_changing_its_resource_reason(tmp_path: Path) -> None:
    source = (
        "import time\n"
        "n = int(input())\n"
        "values = list(map(int, input().split()))\n"
        "blocks = []\n"
        "for _ in range(n):\n"
        "    blocks.append(bytearray(16 * 1024 * 1024))\n"
        "    time.sleep(0.005)\n"
        # Keep the final allocation observable across several 10 ms RSS samples.
        "time.sleep(0.1)\n"
        "print(n)\n"
    )
    config, model = array_workspace(tmp_path, source)
    config.timeout = 2.0
    assert (
        benchmark(
            config,
            sizes=[12],
            repeat=1,
            seed=0,
            pattern="distinct",
            memory_limit_mib=128,
            shrink_budget=2,
        )
        == 1
    )
    session, summary = latest_benchmark(config)
    (diagnostic,) = summary["diagnostics"]
    assert diagnostic["resource_reason"] == "memory_limit"
    assert diagnostic["confirmed"] is True
    assert diagnostic["state"] in {"reduced", "budget_exceeded"}
    assert diagnostic["evaluations"] >= 3
    failure = Path(diagnostic["failure_path"])
    tokens = list(map(int, failure.read_text(encoding="utf-8").split()))
    n, values = tokens[0], tokens[1:]
    assert n == len(values)
    assert 2 < n < 12
    assert len(set(values)) == n
    model.validate_case(values)
    result = read_json(failure.parent / "result.json")
    assert result["resource_reason"] == "memory_limit"
    assert result["confirmed"] is True
    assert len(result["confirmation_runs"]) >= 2
    assert all(run["status"] == "memory_limit" for run in result["confirmation_runs"])
    assert all(run["max_rss_kib"] > 128 * 1024 for run in result["confirmation_runs"])
    assert diagnostic["profile_status"] == "memory_limit"
    profile = read_json(session / diagnostic["directory"] / "profile" / "profile-status.json")
    assert profile["partial"] is True
    assert profile["max_rss_kib"] > 128 * 1024
    assert Path(diagnostic["profile_path"]).is_file()
    assert (session / diagnostic["directory"] / "initial" / "input.txt").read_text().split()[
        0
    ] == "12"
    assert (config.root / "main.py").read_text(encoding="utf-8") == source


def test_custom_generator_retains_fallback_and_rejects_unsupported_pattern(tmp_path: Path) -> None:
    (tmp_path / "cases.py").write_text(
        "from hypothesis import strategies as st\n"
        "def strategy(): return st.integers(1, 5)\n"
        "def serialize(case): return str(case) + '\\n'\n"
        "def benchmark_case(size, seed): return size\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("print(input())\n", encoding="utf-8")
    config = Config(root=tmp_path)
    messages = []
    sink = logger.add(lambda message: messages.append(message.record["message"]))
    try:
        assert benchmark(config, sizes=[2], repeat=1, seed=0) == 0
    finally:
        logger.remove(sink)
    _, summary = latest_benchmark(config)
    assert summary["coverage"] == "custom_default"
    assert [row["pattern"] for row in summary["rows"]] == ["default"]
    assert any("only its default distribution" in message for message in messages)
    with pytest.raises(ValueError, match="does not support --pattern"):
        benchmark(config, sizes=[2], repeat=1, seed=0, pattern="balanced")


def test_array_explicit_size_exceeds_official_bounds_without_weaker_value_checks(
    tmp_path: Path,
) -> None:
    config, model = array_workspace(tmp_path, "print('done')\n")
    assert (
        benchmark(
            config,
            sizes=[13],
            repeat=1,
            seed=0,
            pattern="default",
            profile=False,
        )
        == 0
    )
    session, summary = latest_benchmark(config)
    assert summary["sizes"] == [13]
    lines = (session / "size-13-run-1" / "input.txt").read_text().splitlines()
    assert lines[0] == "13"
    values = list(map(int, lines[1].split()))
    assert len(values) == 13
    assert set(values) <= set(range(1, 14))
    assert len(set(values)) == 2
    with pytest.raises(ValueError, match="official range"):
        model.validate_case(values)
    model.validate_case(values, allow_out_of_range=True)


def test_array_out_of_range_benchmark_shrinking_remains_available(tmp_path: Path) -> None:
    from hypothesis import find, settings

    _, model = array_workspace(tmp_path, "print('done')\n")
    strategy = model.benchmark_strategy(
        15,
        0,
        pattern="balanced",
        allow_out_of_range=True,
    )
    candidate = find(
        strategy,
        lambda case: len(case) > 12,
        settings=settings(max_examples=100, deadline=None, database=None),
    )
    with pytest.raises(ValueError, match="official range"):
        model.serialize(candidate)
    assert model.serialize(candidate, allow_out_of_range=True).startswith(
        str(len(candidate)) + "\n"
    )
