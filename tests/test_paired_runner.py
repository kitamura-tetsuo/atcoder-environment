"""Check overlap, fresh processes, and cleanup of differential comparisons."""

from __future__ import annotations

import os
import selectors
import signal
import sys
import threading
import time
from collections import Counter
from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from atcoder_env.config import Config
from atcoder_env.paired_runner import PairedRunner
from atcoder_env.runner import RunResult, run_program

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")


def command(source: str, *args: str) -> list[str]:
    return [sys.executable, "-u", "-c", source, *args]


def test_solution_and_oracle_overlap_with_real_processes(tmp_path: Path) -> None:
    source = (
        "import pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).touch()\n"
        "while not pathlib.Path(sys.argv[2]).exists(): time.sleep(0.005)\n"
        "print(sum(map(int, sys.stdin.read().split())))\n"
    )
    config = Config(
        root=tmp_path,
        solution=command(source, "solution-started", "oracle-started"),
        oracle=command(source, "oracle-started", "solution-started"),
        timeout=3,
        oracle_timeout=3,
    )
    with PairedRunner(config) as paired:
        actual, oracle = paired.run("3 5\n")
        assert actual is not None
        assert actual.status == oracle.status == "ok"
        assert actual.stdout == oracle.stdout == "8\n"
        assert paired.stats["serial_retries"] == 0
        assert paired.stats["solution_seconds"] > 0
        assert paired.stats["oracle_seconds"] > 0


@pytest.mark.parametrize("parallel", [False, True])
def test_every_case_uses_fresh_interpreters(tmp_path: Path, parallel: bool) -> None:
    source = "import os; counter = globals().get('counter', 0) + 1; print(counter, os.getpid())"
    config = Config(root=tmp_path, solution=command(source), oracle=command(source))
    pids = set()
    with PairedRunner(config, parallel=parallel) as paired:
        for _ in range(3):
            actual, oracle = paired.run("")
            assert actual is not None
            for result in (actual, oracle):
                assert result.status == "ok"
                counter, pid = map(int, result.stdout.split())
                assert counter == 1
                pids.add(pid)
        assert paired.stats["serial_retries"] == 0
    assert len(pids) == 6


@pytest.mark.parametrize(
    ("source", "status"),
    [
        ("raise RuntimeError('broken')", "runtime_error"),
        ("print('x' * 2000)", "output_limit"),
    ],
)
@pytest.mark.parametrize("failed_role", ["solution", "oracle"])
def test_errors_and_output_limits_keep_oracle_precedence(
    tmp_path: Path, source: str, status: str, failed_role: str
) -> None:
    config = Config(root=tmp_path, solution=command("print(1)"), oracle=command("print(2)"))
    config.output_limit = 100
    setattr(config, failed_role, command(source))
    with PairedRunner(config) as paired:
        actual, oracle = paired.run("")
        if failed_role == "oracle":
            assert actual is None
            assert oracle.status == status
        else:
            assert actual is not None
            assert actual.status == status
            assert oracle.status == "ok"
        assert paired.stats["serial_retries"] == 0


@pytest.mark.parametrize("timed_out_role", ["solution", "oracle"])
@pytest.mark.parametrize("serial_status", ["ok", "timeout"])
def test_parallel_timeouts_are_rechecked_serially(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timed_out_role: str, serial_status: str
) -> None:
    calls: list[str] = []
    attempts: Counter[str] = Counter()
    lock = threading.Lock()
    rendezvous = threading.Barrier(2, timeout=5)

    def fake_run_program(arguments, _data, **_kwargs):
        role = arguments[0]
        with lock:
            attempts[role] += 1
            attempt = attempts[role]
            calls.append(role)
        if attempt == 1:
            rendezvous.wait()
        status = ("timeout" if attempt == 1 else serial_status) if role == timed_out_role else "ok"
        return RunResult(arguments, 0 if status == "ok" else -9, "1\n", "", 0.25, status)

    monkeypatch.setattr("atcoder_env.paired_runner.run_program", fake_run_program)
    config = Config(root=tmp_path, solution=["solution"], oracle=["oracle"])
    with PairedRunner(config) as paired:
        actual, oracle = paired.run("input")
        assert paired.stats["serial_retries"] == 1
        assert paired.stats["oracle_seconds"] == 0.5
        if timed_out_role == "oracle" and serial_status == "timeout":
            assert actual is None
            assert oracle.status == "timeout"
            assert calls[-1] == "oracle"
            assert attempts == {"solution": 1, "oracle": 2}
        else:
            assert actual is not None
            assert actual.status == (serial_status if timed_out_role == "solution" else "ok")
            assert oracle.status == "ok"
            assert calls[-2:] == ["oracle", "solution"]
            assert attempts == {"solution": 2, "oracle": 2}
            assert paired.stats["solution_seconds"] == 0.5


def test_cancelled_runner_does_not_launch_program(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CancelledError):
        run_program(
            command("from pathlib import Path; Path('launched').touch()"),
            "",
            cwd=tmp_path,
            cancel_event=cancel,
        )
    assert not (tmp_path / "launched").exists()


DESCENDANT_SOURCE = (
    "import os, pathlib, subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])\n"
    "marker = pathlib.Path(sys.argv[1] + '.pending')\n"
    "marker.write_text(str(os.getpid()) + ' ' + str(child.pid))\n"
    "marker.replace(sys.argv[1] + '.pids')\n"
    "time.sleep(20)\n"
)


def is_running(pid: int) -> bool:
    state = Path(f"/proc/{pid}/stat")
    if state.exists():
        try:
            return state.read_text().split(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def assert_stopped(pids: list[int]) -> None:
    try:
        deadline = time.monotonic() + 2
        while any(is_running(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(is_running(pid) for pid in pids)
    finally:
        for pid in pids:
            if is_running(pid):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("error", [KeyboardInterrupt, ValueError])
def test_main_thread_interrupt_or_error_reaps_both_process_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    original_select = selectors.DefaultSelector.select
    markers = [tmp_path / f"{role}.pids" for role in ("solution", "oracle")]

    def interrupt_when_running(selector, timeout=None):
        events = original_select(selector, timeout)
        if threading.current_thread() is threading.main_thread() and all(
            marker.exists() for marker in markers
        ):
            raise error("stop paired execution")
        return events

    monkeypatch.setattr(selectors.DefaultSelector, "select", interrupt_when_running)
    config = Config(
        root=tmp_path,
        solution=command(DESCENDANT_SOURCE, "solution"),
        oracle=command(DESCENDANT_SOURCE, "oracle"),
        timeout=5,
        oracle_timeout=5,
    )
    with PairedRunner(config) as paired, pytest.raises(error, match="stop paired execution"):
        paired.run("")
    pids = [int(value) for marker in markers for value in marker.read_text().split()]
    assert_stopped(pids)


def test_oracle_failure_cancels_solution_and_its_descendants(tmp_path: Path) -> None:
    oracle_source = (
        "import pathlib, time\n"
        "while not pathlib.Path('solution.pids').exists(): time.sleep(0.005)\n"
        "raise RuntimeError('broken oracle')\n"
    )
    config = Config(
        root=tmp_path,
        solution=command(DESCENDANT_SOURCE, "solution"),
        oracle=command(oracle_source),
        timeout=5,
        oracle_timeout=5,
    )
    with PairedRunner(config) as paired:
        actual, oracle = paired.run("")
        assert actual is None
        assert oracle.status == "runtime_error"
        assert "broken oracle" in oracle.stderr
    assert_stopped([int(value) for value in (tmp_path / "solution.pids").read_text().split()])


def test_interrupt_while_submitting_cancels_scheduled_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    original_submit = ThreadPoolExecutor.submit
    submitted_events = []
    marker = tmp_path / "solution.pids"

    def submit_then_interrupt(executor, function, *args, **kwargs):
        original_submit(executor, function, *args, **kwargs)
        submitted_events.append(kwargs["cancel_event"])
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert marker.exists(), "The submitted solution did not start"
        raise KeyboardInterrupt("interrupted submission")

    monkeypatch.setattr(ThreadPoolExecutor, "submit", submit_then_interrupt)
    config = Config(
        root=tmp_path,
        solution=command(DESCENDANT_SOURCE, "solution"),
        oracle=command("print(1)"),
        timeout=10,
    )
    with PairedRunner(config) as paired, pytest.raises(KeyboardInterrupt, match="submission"):
        paired.run("")
    assert submitted_events and submitted_events[0].is_set()
    assert_stopped([int(value) for value in marker.read_text().split()])
