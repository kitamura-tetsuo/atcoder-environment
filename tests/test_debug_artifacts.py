"""Debug selection stays within the newest run and preserves failure provenance."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from loguru import logger

from atcoder_env.debug_artifacts import find_latest_failures


@pytest.fixture
def warnings():
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink)


def session(root: Path, kind: str = "stress", *, clock: str = "120000", summary=None) -> Path:
    target = root / ".ac" / "runs" / f"20261127T{clock}.000000Z-{kind}-abcdef"
    target.mkdir(parents=True)
    if summary is not None:
        (target / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return target


def failure(folder: Path, *, verdict: str = "WA", data: str = "3\nABC\n", **details) -> Path:
    folder.mkdir(parents=True)
    path = folder / "input.txt"
    path.write_text(data, encoding="utf-8")
    status = {
        "WA": "ok",
        "RE": "runtime_error",
        "TLE": "timeout",
        "MLE": "memory_limit",
        "OLE": "output_limit",
        "timeout": "timeout",
        "memory_limit": "memory_limit",
        "slow": "ok",
        "OK": "ok",
    }[verdict.removeprefix("ORACLE_")]
    record = {"verdict": verdict, "solution": {"status": status}, **details}
    (folder / "result.json").write_text(json.dumps(record), encoding="utf-8")
    return path


def bench_summary(*statuses: str, completed: bool = True) -> dict:
    return {"completed": completed, "rows": [{"status": status} for status in statuses]}


def test_shrunk_stress_failure_is_preferred_and_timestamp_overrides_mtime(tmp_path):
    older = session(tmp_path, clock="110000", summary={"status": "FAIL"})
    failure(older / "failure", data="old\n")
    current = session(tmp_path, summary={"status": "FAIL"})
    failure(current / "initial", data="9\nABCABCABC\n")
    chosen = failure(current / "failure")
    os.utime(older, (2_000_000_000, 2_000_000_000))
    (target,) = find_latest_failures(tmp_path)
    assert target.input_path == chosen
    assert target.session == current
    assert target.kind == "stress"
    assert target.verdict == "WA"
    assert target.role == "solution"
    assert target.reduced is True


@pytest.mark.parametrize(
    ("kind", "summary"),
    [
        ("test", {"failed": 1}),
        ("run", {"status": "WA"}),
        ("stress", {"status": "PASS"}),
        ("stress", {"status": "INCOMPLETE"}),
        ("bench", {"completed": True, "rows": [{"status": "ok"}]}),
    ],
)
def test_newer_run_supersedes_older_failures_including_stray_failure_files(tmp_path, kind, summary):
    older = session(tmp_path, clock="110000", summary={"status": "FAIL"})
    failure(older / "failure")
    newest = session(tmp_path, kind, summary=summary)
    failure(newest / "failure")
    failure(newest / "size-12-run-1", verdict="TLE", pattern="default")
    assert find_latest_failures(tmp_path) == []


def test_interrupted_stress_uses_initial_and_oracle_error_targets_actual_cause(tmp_path):
    current = session(tmp_path)
    initial = failure(current / "initial")
    (target,) = find_latest_failures(tmp_path)
    assert target.input_path == initial
    assert target.reduced is False
    oracle = failure(current / "oracle-error", verdict="ORACLE_RE")
    (current / "summary.json").write_text('{"status":"ERROR"}', encoding="utf-8")
    (target,) = find_latest_failures(tmp_path)
    assert target.input_path == oracle
    assert target.role == "oracle"
    assert target.verdict == "ORACLE_RE"


def test_benchmark_prefers_confirmed_reduction_then_orders_distinct_failed_patterns(tmp_path):
    current = session(tmp_path, "bench", summary=bench_summary("timeout", "runtime_error"))
    failure(current / "size-100-palette-run-1", verdict="TLE", pattern="palette")
    diagnostic = current / "size-100-palette-timeout"
    failure(diagnostic / "initial", verdict="timeout", pattern="palette", data="100\n" + "ABC" * 34)
    minimized = failure(
        diagnostic / "failure", verdict="timeout", pattern="palette", confirmed=True
    )
    runtime_error = failure(current / "size-20-reversed-run-1", verdict="RE", pattern="reversed")
    failure(diagnostic / "profile", verdict="MLE", pattern="profile")
    failure(current / "sources" / "size-1-run-1", verdict="OLE", pattern="default")
    targets = find_latest_failures(tmp_path)
    assert [target.input_path for target in targets] == [runtime_error, minimized]
    assert [target.pattern for target in targets] == ["reversed", "palette"]
    assert [target.verdict for target in targets] == ["RE", "TLE"]
    assert targets[1].reduced is True


def test_unstable_or_unconfirmed_benchmark_reduction_uses_original_failure(tmp_path):
    current = session(tmp_path, "bench", summary=bench_summary("timeout"))
    original = failure(current / "size-100-palette-run-1", verdict="TLE", pattern="palette")
    diagnostic = current / "size-100-palette-timeout"
    failure(diagnostic / "initial", verdict="timeout", pattern="palette")
    failure(
        diagnostic / "failure",
        verdict="timeout",
        pattern="palette",
        confirmed=False,
        minimization_state="unstable",
        solution={"status": "ok"},
    )
    (target,) = find_latest_failures(tmp_path)
    assert target.input_path == original
    assert target.reduced is False


def test_one_original_failure_per_pattern_uses_numeric_size_and_failure_status(tmp_path):
    current = session(tmp_path, "bench", summary=bench_summary("output_limit", "runtime_error"))
    failure(current / "size-100-run-1", verdict="OLE", pattern="default")
    failure(current / "size-2-run-1", verdict="OK", pattern="default")
    smaller = failure(current / "size-2-run-2", verdict="OLE", pattern="default")
    another = failure(current / "size-10-random-run-1", verdict="RE", pattern="random")
    targets = find_latest_failures(tmp_path)
    assert [target.input_path for target in targets] == [smaller, another]
    assert [target.size for target in targets] == [2, 10]


def test_slow_success_and_profiler_errors_are_never_debug_failures(tmp_path):
    current = session(tmp_path, "bench", summary=bench_summary("ok", completed=False))
    failure(current / "size-100-run-1", verdict="OK", pattern="default")
    diagnostic = current / "size-100-slow"
    failure(diagnostic / "initial", verdict="slow", pattern="default")
    failure(diagnostic / "failure", verdict="slow", pattern="default", confirmed=True)
    failure(diagnostic / "profile", verdict="TLE", pattern="default")
    assert find_latest_failures(tmp_path) == []


def test_interrupted_benchmark_can_use_confirmed_checkpoint_or_original_diagnostic(tmp_path):
    current = session(tmp_path, "bench")
    diagnostic = current / "size-100-palette-memory_limit"
    original = failure(diagnostic / "initial", verdict="memory_limit", pattern="palette")
    (target,) = find_latest_failures(tmp_path)
    assert target.input_path == original
    checkpoint = failure(
        diagnostic / "failure", verdict="memory_limit", pattern="palette", confirmed=True
    )
    (target,) = find_latest_failures(tmp_path)
    assert target.input_path == checkpoint
    assert target.verdict == "MLE"


@pytest.mark.parametrize(
    "content", ["{broken", "[]", '{"status":42}', '{"status":[]}', '{"status":{}}']
)
def test_malformed_latest_summary_warns_and_never_revisits_old_failures(
    tmp_path, warnings, content
):
    older = session(tmp_path, clock="110000", summary={"status": "FAIL"})
    failure(older / "failure")
    newest = session(tmp_path)
    failure(newest / "failure")
    (newest / "summary.json").write_text(content, encoding="utf-8")
    assert find_latest_failures(tmp_path) == []
    assert warnings


def test_malformed_or_missing_failure_record_warns_and_falls_back_within_same_run(
    tmp_path, warnings
):
    current = session(tmp_path, summary={"status": "FAIL"})
    selected = failure(current / "initial")
    failure(current / "failure")
    (current / "failure" / "result.json").write_text("[]", encoding="utf-8")
    assert find_latest_failures(tmp_path)[0].input_path == selected
    (current / "initial" / "input.txt").unlink()
    assert find_latest_failures(tmp_path) == []
    assert warnings


def test_inconsistent_confirmed_resource_record_is_not_trusted(tmp_path, warnings):
    current = session(tmp_path, "bench", summary=bench_summary("timeout"))
    original = failure(current / "size-12-run-1", verdict="TLE", pattern="default")
    failure(
        current / "size-12-timeout" / "failure",
        verdict="timeout",
        pattern="default",
        confirmed=True,
        solution={"status": "ok"},
    )
    assert find_latest_failures(tmp_path)[0].input_path == original
    assert warnings


def test_metadata_absolute_paths_and_symlink_inputs_cannot_escape_latest_session(
    tmp_path, warnings
):
    outside = tmp_path / "private-input.txt"
    outside.write_text("private data\n", encoding="utf-8")
    current = session(
        tmp_path,
        "bench",
        summary={**bench_summary("timeout"), "diagnostics": [{"failure_path": str(outside)}]},
    )
    candidate = failure(current / "size-12-run-1", verdict="TLE", pattern="default")
    candidate.unlink()
    candidate.symlink_to(outside)
    assert find_latest_failures(tmp_path) == []
    assert warnings


def test_symbolic_link_newest_session_blocks_old_failure_reuse(tmp_path, warnings):
    older = session(tmp_path, clock="110000", summary={"status": "FAIL"})
    failure(older / "failure")
    external = tmp_path / "outside-history"
    external.mkdir()
    failure(external / "failure")
    (older.parent / "20261127T120000.000000Z-stress-abcdef").symlink_to(
        external, target_is_directory=True
    )
    assert find_latest_failures(tmp_path) == []
    assert warnings


def test_absent_history_and_unrelated_directories_produce_sample_fallback(tmp_path):
    assert find_latest_failures(tmp_path) == []
    (tmp_path / ".ac" / "runs" / "notes").mkdir(parents=True)
    assert find_latest_failures(tmp_path) == []


def test_legacy_benchmark_summary_without_completion_flag_keeps_saved_failure(tmp_path):
    current = session(
        tmp_path,
        "bench",
        summary={"seed": 0, "rows": [{"status": "timeout"}], "checks_correctness": False},
    )
    selected = failure(current / "size-100-run-1", verdict="TLE")
    assert find_latest_failures(tmp_path)[0].input_path == selected


def test_explicit_invalid_benchmark_completion_flag_is_not_a_legacy_summary(tmp_path, warnings):
    current = session(
        tmp_path, "bench", summary={"rows": [{"status": "timeout"}], "completed": "true"}
    )
    failure(current / "size-100-run-1", verdict="TLE")
    assert find_latest_failures(tmp_path) == []
    assert warnings
