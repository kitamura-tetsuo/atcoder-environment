"""Resource shrinking must retain its symptom and independently reproduce it."""

from collections import Counter

import pytest
from hypothesis import strategies as st

import atcoder_env.bench_minimize as minimizer
from atcoder_env.bench_minimize import minimize_resource_case
from atcoder_env.constraint_model import IntegerArrayConstraints
from atcoder_env.runner import RunResult


def serialize(case):
    return f"{len(case)}\n{' '.join(map(str, case))}\n"


def values(size):
    return list(range(1, size + 1))


def result(status="ok", elapsed=0.01):
    return RunResult(
        command=["python", "main.py"],
        returncode=0 if status == "ok" else -9,
        stdout="",
        stderr="",
        elapsed_seconds=elapsed,
        status=status,
    )


def reduce(run, **kwargs):
    options = {
        "strategy": st.integers(2, 100).map(values),
        "serialize": serialize,
        "run": run,
        "reason": "timeout",
        "slow_seconds": 0.5,
        "seed": 0,
        "budget": 5,
        "max_examples": 100,
        "max_executions": 500,
    }
    options.update(kwargs)
    return minimize_resource_case(values(100), **options)


def test_timeout_shrinks_large_cases_and_freshly_confirms_the_result():
    calls = Counter()
    saved = []

    def run(data):
        size = int(data.splitlines()[0])
        calls[size] += 1
        return result("timeout" if size >= 37 else "ok")

    reduced = reduce(run, on_improvement=lambda case, data, runs: saved.append(len(case)))
    assert reduced.state == "reduced"
    assert len(reduced.case) == 37
    assert reduced.confirmed
    assert calls[37] >= 4
    assert len(reduced.runs) == 2
    assert saved[0] == 100 and saved[-1] == 37
    assert saved == sorted(set(saved), reverse=True)


def test_memory_reduction_cannot_turn_into_timeout_or_runtime_error():
    def run(data):
        size = int(data.splitlines()[0])
        if size >= 29:
            return result("memory_limit")
        if size >= 10:
            return result("timeout")
        return result("runtime_error")

    reduced = reduce(run, reason="memory_limit")
    assert reduced.confirmed
    assert len(reduced.case) == 29
    assert all(run.status == "memory_limit" for run in reduced.runs)


def test_slow_reduction_preserves_success_and_uses_unprofiled_elapsed_time():
    def run(data):
        size = int(data.splitlines()[0])
        return result("ok" if size >= 10 else "timeout", elapsed=size / 100)

    reduced = reduce(run, reason="slow", slow_seconds=0.41)
    assert len(reduced.case) == 41
    assert reduced.confirmed
    assert all(run.status == "ok" for run in reduced.runs)


def test_search_budget_preserves_then_reconfirms_the_best_input(monkeypatch):
    clock = [0.0]
    saved = []
    monkeypatch.setattr(minimizer, "monotonic", lambda: clock[0])

    def run(data):
        clock[0] += 0.1
        return result("timeout" if int(data.splitlines()[0]) >= 50 else "ok")

    reduced = reduce(
        run,
        budget=0.35,
        on_improvement=lambda case, data, runs: saved.append(len(case)),
    )
    assert reduced.state == "budget_exceeded"
    assert reduced.confirmed
    assert saved[0] == 100
    assert len(reduced.runs) == 2
    assert len(reduced.case) >= 50


def test_execution_cap_also_limits_hypothesis_shrinking_and_keeps_verification_slots():
    reduced = reduce(lambda data: result("timeout"), max_executions=6)
    assert reduced.state == "budget_exceeded"
    assert reduced.evaluations <= 6
    assert reduced.confirmed
    assert len(reduced.case) == 100


def test_inconsistent_initial_failure_is_not_reported_as_minimized():
    calls = 0

    def run(data):
        nonlocal calls
        calls += 1
        return result("timeout" if calls == 1 else "ok")

    reduced = reduce(run)
    assert reduced.state == "unstable"
    assert not reduced.confirmed
    assert reduced.candidates == 0
    assert reduced.flaky_candidates == 1


def test_flaky_final_candidate_falls_back_only_after_rechecking_original():
    calls = Counter()

    def run(data):
        size = int(data.splitlines()[0])
        calls[size] += 1
        if size == 100:
            return result("timeout")
        return result("timeout" if size >= 20 and calls[size] <= 2 else "ok")

    reduced = reduce(run)
    assert reduced.state == "unstable"
    assert reduced.confirmed
    assert len(reduced.case) == 100
    assert calls[100] == 4
    assert reduced.flaky_candidates == 1
    assert all(run.status == "timeout" for run in reduced.runs)


def test_no_rediscovery_does_not_lose_the_confirmed_original():
    reduced = reduce(
        lambda data: result("timeout" if int(data.splitlines()[0]) == 100 else "ok"),
        strategy=st.integers(2, 10).map(values),
    )
    assert reduced.state == "no_example"
    assert reduced.confirmed
    assert len(reduced.case) == 100


def test_initial_and_smaller_inputs_are_saved_before_interruption(monkeypatch):
    saved = []

    def interrupted_find(strategy, predicate, **kwargs):
        assert predicate(values(50))
        raise KeyboardInterrupt

    monkeypatch.setattr(minimizer, "find", interrupted_find)
    with pytest.raises(KeyboardInterrupt):
        reduce(
            lambda data: result("timeout"),
            on_improvement=lambda case, data, runs: saved.append(len(case)),
        )
    assert saved == [100, 50]


def test_model_strategy_reduces_size_and_cardinality_above_the_stress_cap():
    model = IntegerArrayConstraints(
        size_name="N",
        size_min=2,
        size_max=1000,
        values_name="A",
        values_min=1,
        values_max="N",
        min_distinct=2,
        stress_max_size=30,
    )
    initial = model.benchmark_case(1000, 0, pattern="palette")
    statuses = Counter()

    def run(data):
        size, *case = map(int, data.split())
        assert size == len(case)
        model.validate_case(case)
        if size * len(set(case)) >= 10_000:
            status = "memory_limit"
        elif size >= 30:
            status = "timeout"
        else:
            status = "ok"
        statuses[status] += 1
        return result(status)

    reduced = minimize_resource_case(
        initial,
        strategy=model.benchmark_strategy(1000, 0, pattern="palette"),
        serialize=model.serialize,
        run=run,
        reason="memory_limit",
        slow_seconds=0.5,
        seed=0,
        budget=5,
        max_examples=100,
        max_executions=1000,
    )
    assert reduced.state == "reduced"
    assert reduced.confirmed
    assert len(reduced.case) == 100
    assert len(set(reduced.case)) == 100
    assert model.effective_stress_max_size < len(reduced.case) < len(initial)
    assert statuses["timeout"] > 0
    assert all(run.status == "memory_limit" for run in reduced.runs)


def test_size_only_stage_rediscovers_original_seed_before_parameter_search():
    model = IntegerArrayConstraints("N", 2, 100, "A", 1, "N", min_distinct=2)
    initial = model.benchmark_case(100, 19, pattern="random")

    def run(data):
        size, *case = map(int, data.split())
        original_distribution = model.benchmark_case(size, 19, pattern="random")
        return result("timeout" if size >= 37 and case == original_distribution else "ok")

    reduced = minimize_resource_case(
        initial,
        preliminary_strategy=model.benchmark_strategy(100, 19, pattern="random", size_only=True),
        strategy=model.benchmark_strategy(100, 19, pattern="random"),
        serialize=model.serialize,
        run=run,
        reason="timeout",
        slow_seconds=0.5,
        budget=5,
        max_executions=1000,
    )
    assert reduced.state == "reduced"
    assert reduced.confirmed
    assert reduced.case == model.benchmark_case(37, 19, pattern="random")


def test_parameter_stage_reduces_cardinality_after_size_stage():
    model = IntegerArrayConstraints("N", 2, 1000, "A", 1, "N", min_distinct=2)
    initial = model.benchmark_case(1000, 0, pattern="palette")
    improvements = []

    def run(data):
        size, *case = map(int, data.split())
        model.validate_case(case)
        return result("memory_limit" if size >= 200 and len(set(case)) >= 5 else "timeout")

    reduced = minimize_resource_case(
        initial,
        preliminary_strategy=model.benchmark_strategy(1000, 0, pattern="palette", size_only=True),
        strategy=model.benchmark_strategy(1000, 0, pattern="palette"),
        serialize=model.serialize,
        run=run,
        reason="memory_limit",
        slow_seconds=0.5,
        budget=5,
        max_executions=1000,
        on_improvement=lambda case, data, runs: improvements.append((len(case), len(set(case)))),
    )
    assert reduced.state == "reduced"
    assert reduced.confirmed
    assert (200, 200) in improvements
    assert (len(reduced.case), len(set(reduced.case))) == (200, 5)
    assert all(run.status == "memory_limit" for run in reduced.runs)


def test_stages_share_one_time_budget_and_preserve_first_stage_progress(monkeypatch):
    clock = [0.0]
    stages = []
    monkeypatch.setattr(minimizer, "monotonic", lambda: clock[0])

    def run(data):
        clock[0] += 0.1
        return result("timeout")

    def staged_find(strategy, predicate, **kwargs):
        stages.append(strategy)
        candidate = values(50 if len(stages) == 1 else 25)
        assert predicate(candidate)
        return candidate

    monkeypatch.setattr(minimizer, "find", staged_find)
    reduced = reduce(run, preliminary_strategy=st.integers(), budget=0.45)
    assert len(stages) == 2
    assert reduced.state == "budget_exceeded"
    assert reduced.confirmed
    assert len(reduced.case) == 50
    assert reduced.evaluations == 7


def test_no_example_in_second_stage_keeps_a_successful_first_reduction():
    reduced = reduce(
        lambda data: result("timeout" if int(data.splitlines()[0]) >= 37 else "ok"),
        preliminary_strategy=st.integers(2, 100).map(values),
        strategy=st.integers(2, 5).map(values),
    )
    assert reduced.state == "reduced"
    assert reduced.confirmed
    assert len(reduced.case) == 37
