"""Bounded Hypothesis shrinking of reproducible resource-heavy inputs."""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any

from hypothesis import Phase, find, settings
from hypothesis.errors import Flaky, NoSuchExample
from hypothesis.strategies import SearchStrategy
from loguru import logger

from atcoder_env.runner import RunResult

_RESOURCE_REASONS = {"slow", "timeout", "memory_limit"}


class _ReductionBudgetExceeded(BaseException):
    """Stop Hypothesis without turning the search budget into a test failure."""


@dataclass(frozen=True)
class ResourceReduction:
    """The best input confirmed within a bounded search, never a global minimum."""

    case: Any
    input_data: str
    runs: tuple[RunResult, ...]
    reason: str
    state: str
    evaluations: int
    candidates: int
    elapsed_seconds: float
    confirmed: bool
    flaky_candidates: int


def matches_resource_reason(result: RunResult, reason: str, slow_seconds: float) -> bool:
    """Keep timeouts, slow successful runs, and memory failures distinct."""
    if reason not in _RESOURCE_REASONS:
        raise ValueError(f"Unknown resource failure reason: {reason}")
    if reason == "slow":
        return result.status == "ok" and result.elapsed_seconds >= slow_seconds
    return result.status == reason


def minimize_resource_case(
    initial_case: Any,
    *,
    strategy: SearchStrategy,
    preliminary_strategy: SearchStrategy | None = None,
    serialize: Callable[[Any], str],
    run: Callable[[str], RunResult],
    reason: str,
    slow_seconds: float,
    seed: int = 0,
    budget: float = 30.0,
    max_examples: int = 100,
    max_executions: int | None = None,
    confirmations: int = 2,
    initial_runs: Sequence[RunResult] = (),
    on_improvement: Callable[[Any, str, tuple[RunResult, ...]], None] | None = None,
) -> ResourceReduction:
    """Shrink an input while preserving a repeatedly observed resource symptom.

    ``strategy`` must generate valid inputs, including large inputs when needed;
    the ordinary small differential-test strategy is often unsuitable. The
    predicate uses the original, unprofiled program and never calls an oracle.
    An optional preliminary strategy can shrink size while keeping the original
    distribution and seed, before the full strategy simplifies other parameters.
    Both stages share the same cache, time budget, and execution cap.

    Confirmed classifications are cached only for this search, so Hypothesis
    sees a stable predicate despite timing noise. The final input is checked
    again without the cache. If it fails to reproduce, the original input is
    independently checked before falling back to it. A negative confirmation
    never changes a memory failure into a timeout or a slow successful run.

    The time budget is checked between bounded ``run`` calls. Final verification
    may add up to four such calls. The execution cap includes these calls and
    applies during shrinking too, unlike Hypothesis's generation-only limit.
    ``on_improvement`` receives the confirmed original and each smaller input
    immediately, allowing the caller to preserve progress on interruption.
    """
    if reason not in _RESOURCE_REASONS:
        raise ValueError(f"Unknown resource failure reason: {reason}")
    if not math.isfinite(slow_seconds) or slow_seconds <= 0:
        raise ValueError("slow_seconds must be a positive finite number")
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("Shrink budget must be a positive finite number")
    if type(seed) is not int:
        raise ValueError("Shrink seed must be an integer")
    if type(max_examples) is not int or max_examples < 1:
        raise ValueError("max_examples must be a positive integer")
    if type(confirmations) is not int or confirmations < 2:
        raise ValueError("Resource shrinking requires at least two confirmations")
    if max_executions is None:
        max_executions = max_examples * confirmations + 3 * confirmations
    if type(max_executions) is not int or max_executions < 3 * confirmations:
        raise ValueError("max_executions must allow initial and final confirmations")

    started = monotonic()
    deadline = started + budget
    evaluations = 0
    candidates = 0
    flaky_candidates = 0
    last_progress = started
    # Digests keep the cache small even when every candidate has 300,000 values.
    labels: dict[bytes, bool] = {}
    best_case = initial_case
    best_data = serialize(initial_case)
    if not isinstance(best_data, str):
        raise ValueError("serialize(case) must return str")
    original_data = best_data
    best_runs: tuple[RunResult, ...] = ()

    def rank(data: str) -> tuple[int, int, str]:
        return len(data.split()), len(data.encode("utf-8")), data

    best_rank = rank(best_data)

    def check_budget() -> None:
        # Reserve final confirmation of both the best and original input.
        if monotonic() >= deadline or evaluations >= max_executions - 2 * confirmations:
            raise _ReductionBudgetExceeded

    def execute(data: str, *, searching: bool) -> RunResult:
        nonlocal evaluations, last_progress
        if searching:
            check_budget()
        if evaluations >= max_executions:
            raise _ReductionBudgetExceeded
        evaluations += 1
        result = run(data)
        now = monotonic()
        if now - last_progress >= 20.0:
            logger.info(
                "Hypothesis {} reduction: {} executions, {} candidate inputs, {:.1f}s elapsed",
                reason,
                evaluations,
                candidates,
                now - started,
            )
            last_progress = now
        return result

    def confirm(
        data: str, *, searching: bool, previous: Sequence[RunResult] = ()
    ) -> tuple[bool, tuple[RunResult, ...]]:
        nonlocal flaky_candidates
        runs = list(previous)
        if runs and not all(matches_resource_reason(r, reason, slow_seconds) for r in runs):
            if any(matches_resource_reason(r, reason, slow_seconds) for r in runs):
                flaky_candidates += 1
            return False, tuple(runs)
        while len(runs) < confirmations:
            result = execute(data, searching=searching)
            runs.append(result)
            if not matches_resource_reason(result, reason, slow_seconds):
                if len(runs) > 1:
                    flaky_candidates += 1
                return False, tuple(runs)
        return True, tuple(runs)

    def finish(state: str, confirmed: bool) -> ResourceReduction:
        return ResourceReduction(
            case=best_case,
            input_data=best_data,
            runs=best_runs,
            reason=reason,
            state=state,
            evaluations=evaluations,
            candidates=candidates,
            elapsed_seconds=monotonic() - started,
            confirmed=confirmed,
            flaky_candidates=flaky_candidates,
        )

    initial_confirmed, best_runs = confirm(original_data, searching=False, previous=initial_runs)
    if not initial_confirmed:
        logger.warning("Initial {} input did not consistently reproduce; shrinking skipped", reason)
        return finish("unstable", False)
    labels[hashlib.sha256(original_data.encode("utf-8")).digest()] = True
    if on_improvement is not None:
        on_improvement(best_case, best_data, best_runs)

    def predicate(case: Any) -> bool:
        nonlocal best_case, best_data, best_rank, best_runs, candidates
        check_budget()
        data = serialize(case)
        if not isinstance(data, str):
            raise ValueError("serialize(case) must return str")
        digest = hashlib.sha256(data.encode("utf-8")).digest()
        if digest in labels:
            return labels[digest]
        candidates += 1
        accepted, runs = confirm(data, searching=True)
        labels[digest] = accepted
        if accepted and rank(data) < best_rank:
            best_case, best_data, best_runs = case, data, runs
            best_rank = rank(data)
            if on_improvement is not None:
                on_improvement(best_case, best_data, best_runs)
        return accepted

    state = "unchanged"
    search_random = random.Random(seed)
    search_settings = settings(
        max_examples=max_examples,
        deadline=None,
        database=None,
        phases=(Phase.generate, Phase.shrink),
        derandomize=False,
    )
    stages = [strategy] if preliminary_strategy is None else [preliminary_strategy, strategy]
    found = False
    try:
        for stage in stages:
            check_budget()
            try:
                find(stage, predicate, random=search_random, settings=search_settings)
            except NoSuchExample:
                continue
            found = True
        if best_data != original_data:
            state = "reduced"
        elif not found:
            state = "no_example"
    except _ReductionBudgetExceeded:
        state = "budget_exceeded"
        logger.info("Hypothesis {} reduction budget reached; preserving the best input", reason)
    except Flaky:
        state = "unstable"
        logger.warning("Hypothesis could not consistently replay the reduction strategy")

    confirmed, checked_runs = confirm(best_data, searching=False)
    best_runs = checked_runs
    if not confirmed:
        if len(checked_runs) == 1:
            flaky_candidates += 1
        logger.warning("Reduced {} input did not reproduce on final confirmation", reason)
        state = "unstable"
        if best_data != original_data:
            best_case, best_data = initial_case, original_data
            confirmed, best_runs = confirm(original_data, searching=False)
            if not confirmed and len(best_runs) == 1:
                flaky_candidates += 1
    return finish(state, confirmed)
