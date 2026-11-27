"""Run an unchanged CPython script under line_profiler in a separate process."""

from __future__ import annotations

import csv
import json
import os
import signal
import sys
import tokenize
from pathlib import Path
from threading import Event, Thread
from types import CellType, CodeType, FunctionType, ModuleType

import psutil
from line_profiler import LineProfiler


class ProfileDeadline(BaseException):
    """Stop profiling and retain completed line measurements."""


class ProfileMemoryLimit(BaseException):
    """Stop profiling at the sampled memory cap and retain line measurements."""


def _register(profiler: LineProfiler, code: CodeType) -> None:
    # Register the module body as well as functions and nested code objects.
    closure = tuple(CellType() for _ in code.co_freevars) or None
    profiler.add_function(FunctionType(code, {}, closure=closure))
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            _register(profiler, constant)


def profile_script(
    report: Path,
    timeout: float,
    script: Path,
    args: list[str],
    *,
    memory_limit_mib: int | None = None,
) -> int:
    if memory_limit_mib is not None and (
        type(memory_limit_mib) is not int or memory_limit_mib <= 0
    ):
        raise ValueError("memory_limit_mib must be a positive integer or None")
    script = script.resolve()
    with tokenize.open(script) as handle:
        source = handle.read()
    code = compile(source, str(script), "exec")
    profiler = LineProfiler()
    _register(profiler, code)
    main = ModuleType("__main__")
    main.__file__ = str(script)
    main.__package__ = None
    main.__spec__ = None
    main.__loader__ = None
    main.__cached__ = None
    sys.modules["__main__"] = main
    sys.argv = [str(script), *args]
    sys.path[0] = str(script.parent)
    status = "ok"
    active = False
    memory_exceeded = False
    max_rss_kib: int | None = None
    monitor_error: str | None = None
    stop_monitor = Event()
    memory_limit_kib = memory_limit_mib * 1024 if memory_limit_mib is not None else None
    observed_process = psutil.Process()

    def current_memory_kib() -> int:
        resident_bytes = 0
        for member in [observed_process, *observed_process.children(recursive=True)]:
            try:
                resident_bytes += member.memory_info().rss
            except psutil.NoSuchProcess:
                continue
        return (resident_bytes + 1023) // 1024

    def deadline(signum, frame):
        if active:
            raise ProfileDeadline

    def memory_limit(signum, frame):
        nonlocal memory_exceeded
        memory_exceeded = True
        # A pending signal during teardown must not interrupt report writing.
        if active:
            raise ProfileMemoryLimit

    def monitor_memory() -> None:
        nonlocal max_rss_kib, memory_exceeded, monitor_error
        try:
            while not stop_monitor.wait(0.01):
                max_rss_kib = max(max_rss_kib or 0, current_memory_kib())
                if memory_limit_kib is not None and max_rss_kib > memory_limit_kib:
                    memory_exceeded = True
                    if not stop_monitor.is_set():
                        os.kill(os.getpid(), signal.SIGUSR1)
                    return
        except psutil.Error as error:
            monitor_error = f"{type(error).__name__}: {error}"

    previous_deadline = signal.signal(signal.SIGALRM, deadline)
    previous_memory = None
    monitor = None
    if memory_limit_kib is not None:
        previous_memory = signal.signal(signal.SIGUSR1, memory_limit)
        monitor = Thread(target=monitor_memory, name="ac-profile-memory", daemon=True)
    try:
        profiler.enable_by_count()
        active = True
        if monitor is not None:
            max_rss_kib = current_memory_kib()
            monitor.start()
            if max_rss_kib > memory_limit_kib:
                raise ProfileMemoryLimit
        signal.setitimer(signal.ITIMER_REAL, timeout)
        exec(code, main.__dict__)
    except ProfileDeadline:
        status = "timeout"
    except ProfileMemoryLimit:
        status = "memory_limit"
    except SystemExit as error:
        if error.code not in (None, 0):
            status = "runtime_error"
        raise
    except BaseException:
        status = "runtime_error"
        raise
    finally:
        active = False
        signal.setitimer(signal.ITIMER_REAL, 0)
        profiler.disable_by_count()
        stop_monitor.set()
        if monitor is not None:
            if monitor.ident is not None:
                monitor.join()
            # Only measure current post-exec RSS. RUSAGE_SELF high-water marks
            # can include the launcher parent's memory from before exec.
            max_rss_kib = max(max_rss_kib or 0, current_memory_kib())
            memory_exceeded |= max_rss_kib > memory_limit_kib
            if status == "ok" and memory_exceeded:
                status = "memory_limit"
            if status == "ok" and monitor_error is not None:
                status = "memory_monitor_error"
            signal.signal(signal.SIGUSR1, previous_memory)
        signal.signal(signal.SIGALRM, previous_deadline)
        stats = profiler.get_stats()
        lines = source.splitlines()
        rows = []
        for (filename, first_line, function), timings in stats.timings.items():
            total = sum(ticks for _, _, ticks in timings)
            for line, hits, ticks in timings:
                seconds = ticks * stats.unit
                rows.append(
                    {
                        "file": filename,
                        "function": function,
                        "first_line": first_line,
                        "line": line,
                        "hits": hits,
                        "seconds": seconds,
                        "seconds_per_hit": seconds / hits if hits else 0.0,
                        "percent_of_function": 100 * ticks / total if total else 0.0,
                        "source": lines[line - 1] if 1 <= line <= len(lines) else "",
                    }
                )
        rows.sort(key=lambda row: row["seconds"], reverse=True)
        fields = [
            "file",
            "function",
            "first_line",
            "line",
            "hits",
            "seconds",
            "seconds_per_hit",
            "percent_of_function",
            "source",
        ]
        with (report / "line-profile.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        (report / "profile-status.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "partial": status != "ok",
                    "timeout": timeout,
                    "memory_limit_mib": memory_limit_mib,
                    "max_rss_kib": max_rss_kib,
                    "memory_monitor_error": monitor_error,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return {"timeout": 124, "memory_limit": 125, "memory_monitor_error": 126}.get(status, 0)


if __name__ == "__main__":
    raise SystemExit(
        profile_script(
            Path(sys.argv[1]),
            float(sys.argv[2]),
            Path(sys.argv[4]),
            sys.argv[5:],
            memory_limit_mib=int(sys.argv[3]),
        )
    )
