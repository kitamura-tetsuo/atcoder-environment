"""Run each differential comparison with isolated, overlapping child processes."""

from __future__ import annotations

from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from contextlib import suppress
from threading import Event
from typing import Self

from atcoder_env.config import Config
from atcoder_env.runner import RunResult, run_program


class PairedRunner:
    """Overlap solution and oracle startup without reusing interpreter state."""

    def __init__(self, config: Config, *, parallel: bool = True) -> None:
        self.config = config
        self.parallel = parallel
        self.stats: dict[str, float | int] = {
            "solution_seconds": 0.0,
            "oracle_seconds": 0.0,
            "serial_retries": 0,
        }
        self._executor: ThreadPoolExecutor | None = None
        self._cancel_event: Event | None = None

    def __enter__(self) -> Self:
        if self.parallel:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ac-solution")
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def _execute(
        self, data: str, *, oracle: bool = False, cancel_event: Event | None = None
    ) -> RunResult:
        config = self.config
        return run_program(
            config.oracle if oracle else config.solution,
            data,
            cwd=config.root,
            timeout=config.oracle_timeout if oracle else config.timeout,
            output_limit=config.output_limit,
            cancel_event=cancel_event,
        )

    def _record(self, result: RunResult, *, oracle: bool = False) -> RunResult:
        # Only the calling thread updates statistics.
        key = "oracle_seconds" if oracle else "solution_seconds"
        self.stats[key] += result.elapsed_seconds
        return result

    def _sequential(self, data: str) -> tuple[RunResult | None, RunResult]:
        oracle = self._record(self._execute(data, oracle=True), oracle=True)
        actual = self._record(self._execute(data)) if oracle.status == "ok" else None
        return actual, oracle

    def run(self, data: str) -> tuple[RunResult | None, RunResult]:
        """Return (solution, oracle); failed oracles suppress the solution result.

        A timeout during overlap is retried serially after both children stop,
        so CPU contention alone does not produce a solution or oracle timeout.
        """
        if not self.parallel:
            return self._sequential(data)
        if self._executor is None:
            raise RuntimeError("Use PairedRunner inside a with statement")
        cancel_event = Event()
        self._cancel_event = cancel_event
        future: Future[RunResult] | None = None
        try:
            future = self._executor.submit(self._execute, data, cancel_event=cancel_event)
            oracle = self._record(self._execute(data, oracle=True), oracle=True)
            if oracle.status != "ok":
                cancel_event.set()
                actual = None
                with suppress(CancelledError):
                    actual = self._record(future.result())
            else:
                actual = self._record(future.result())
        except BaseException:
            cancel_event.set()
            # Reap the worker before propagating an interrupt or oracle exception.
            if future is not None:
                with suppress(CancelledError):
                    future.exception()
            raise
        if oracle.status == "timeout" or (
            oracle.status == "ok" and actual is not None and actual.status == "timeout"
        ):
            self.stats["serial_retries"] += 1
            return self._sequential(data)
        return actual if oracle.status == "ok" else None, oracle
