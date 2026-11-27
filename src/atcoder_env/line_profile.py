"""Run an unchanged CPython script under line_profiler in a separate process."""

from __future__ import annotations

import csv
import json
import signal
import sys
import tokenize
from pathlib import Path
from types import CellType, CodeType, FunctionType, ModuleType

from line_profiler import LineProfiler


class ProfileDeadline(BaseException):
    """Stop profiling and retain completed line measurements."""


def _deadline(signum, frame):
    raise ProfileDeadline


def _register(profiler: LineProfiler, code: CodeType) -> None:
    # Register the module body as well as functions and nested code objects.
    closure = tuple(CellType() for _ in code.co_freevars) or None
    profiler.add_function(FunctionType(code, {}, closure=closure))
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            _register(profiler, constant)


def profile_script(report: Path, timeout: float, script: Path, args: list[str]) -> int:
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
    previous = signal.signal(signal.SIGALRM, _deadline)
    try:
        profiler.enable_by_count()
        signal.setitimer(signal.ITIMER_REAL, timeout)
        exec(code, main.__dict__)
    except ProfileDeadline:
        status = "timeout"
    except SystemExit as error:
        if error.code not in (None, 0):
            status = "runtime_error"
        raise
    except BaseException:
        status = "runtime_error"
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        profiler.disable_by_count()
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
            json.dumps({"status": status, "partial": status != "ok", "timeout": timeout}) + "\n",
            encoding="utf-8",
        )
    return 124 if status == "timeout" else 0


if __name__ == "__main__":
    raise SystemExit(
        profile_script(Path(sys.argv[1]), float(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:])
    )
