"""Exercise actual child processes, resource limits, and judge comparisons."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from atcoder_env.runner import RunResult, compare_output, run_program, verdict

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")


def run_python(tmp_path: Path, source: str, data: str = "", **kwargs: object) -> RunResult:
    return run_program([sys.executable, "-u", "-c", source], data, cwd=tmp_path, **kwargs)


def test_captures_stdout_stderr_and_returncode(tmp_path: Path) -> None:
    result = run_python(
        tmp_path,
        "import sys; print(sys.stdin.read().strip()[::-1]); print('debug', file=sys.stderr)",
        "hello\n",
    )
    assert result.status == "ok"
    assert result.returncode == 0
    assert result.stdout == "olleh\n"
    assert result.stderr == "debug\n"
    assert result.elapsed_seconds >= 0
    assert result.max_rss_kib is None
    assert json.loads(json.dumps(result.to_dict()))["status"] == "ok"
    assert verdict(result, "olleh") == "AC"
    assert verdict(result, "hello") == "WA"


def test_large_stdin_and_both_outputs_do_not_deadlock(tmp_path: Path) -> None:
    data = "x" * 200_000
    result = run_python(
        tmp_path,
        "import sys; sys.stderr.write('e' * 100000); "
        "sys.stdout.write(sys.stdin.read()); sys.stderr.write('tail')",
        data,
    )
    assert result.status == "ok"
    assert result.stdout == data
    assert result.stderr == "e" * 100_000 + "tail"


def test_program_can_close_stdin_early(tmp_path: Path) -> None:
    result = run_python(tmp_path, "print('done')", "unused" * 100_000)
    assert result.status == "ok"
    assert result.stdout == "done\n"


def test_runtime_error_preserves_traceback(tmp_path: Path) -> None:
    result = run_python(tmp_path, "raise ValueError('broken')")
    assert result.status == "runtime_error"
    assert result.returncode != 0
    assert "ValueError: broken" in result.stderr
    assert verdict(result, "") == "RE"


def test_missing_interpreter_is_reported(tmp_path: Path) -> None:
    result = run_program([str(tmp_path / "does-not-exist")], "", cwd=tmp_path)
    assert result.status == "runtime_error"
    assert result.returncode is None
    assert "FileNotFoundError" in result.stderr


def test_timeout_preserves_partial_output(tmp_path: Path) -> None:
    result = run_python(tmp_path, "import time; print('before'); time.sleep(10)", timeout=0.2)
    assert result.status == "timeout"
    assert result.stdout == "before\n"
    assert result.elapsed_seconds < 3
    assert verdict(result, "before") == "TLE"


def test_timeout_after_program_closes_output_pipes(tmp_path: Path) -> None:
    result = run_python(
        tmp_path,
        "import os, time; os.close(1); os.close(2); time.sleep(10)",
        timeout=0.2,
    )
    assert result.status == "timeout"
    assert result.elapsed_seconds < 3


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_limits_each_output_stream(tmp_path: Path, stream: str) -> None:
    result = run_python(
        tmp_path,
        f"import sys\nwhile True: sys.{stream}.write('x' * 10000)",
        output_limit=1234,
    )
    assert result.status == "output_limit"
    assert len(result.stdout.encode()) + len(result.stderr.encode()) == 1234
    assert result.elapsed_seconds < 3
    assert verdict(result, "") == "OLE"


def test_limit_is_shared_by_stdout_and_stderr(tmp_path: Path) -> None:
    result = run_python(
        tmp_path,
        "import sys; sys.stdout.write('a' * 700); sys.stderr.write('b' * 700)",
        output_limit=1000,
    )
    assert result.status == "output_limit"
    assert len(result.stdout) + len(result.stderr) == 1000


def test_exactly_output_limit_is_allowed(tmp_path: Path) -> None:
    result = run_python(tmp_path, "import sys; sys.stdout.write('x' * 1000)", output_limit=1000)
    assert result.status == "ok"
    assert len(result.stdout) == 1000


def is_running(pid: int) -> bool:
    """Treat unreaped zombies as stopped when inspecting Linux processes."""
    state_path = Path(f"/proc/{pid}/stat")
    if state_path.exists():
        try:
            return state_path.read_text().split(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("parent_exit", [False, True])
def test_background_children_are_killed(tmp_path: Path, parent_exit: bool) -> None:
    source = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])\n"
        "print(child.pid, flush=True)\n"
    )
    if not parent_exit:
        source += "time.sleep(20)\n"
    result = run_python(tmp_path, source, timeout=0.3)
    child_pid = int(result.stdout.strip())
    try:
        assert result.status == ("ok" if parent_exit else "timeout")
        deadline = time.monotonic() + 1
        while is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not is_running(child_pid)
    finally:
        if is_running(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def test_tokens_and_exact_modes() -> None:
    assert compare_output(" 1 2\n3\n", "1\n2 3")
    assert not compare_output("1.0", "1")
    assert not compare_output("1 2", "1 2 3")
    assert not compare_output("1\n", "1", mode="exact")
    assert compare_output("1\n", "1\n", mode="exact")
    assert compare_output("\n \t", "")


def test_float_mode_mixes_text_and_numbers() -> None:
    assert compare_output("YES 1.00000001", "YES 1", mode="float", atol=1e-7)
    assert not compare_output("NO 1", "YES 1", mode="float")
    assert not compare_output("1.01", "1", mode="float", atol=1e-4)
    assert compare_output("1e8", "100000001", mode="float", rtol=1e-7)
    assert compare_output("0.00000001", "0", mode="float", atol=1e-7)


@pytest.mark.parametrize("token", ["nan", "NaN", "inf", "-Infinity"])
def test_nonfinite_float_tokens_are_never_equal(token: str) -> None:
    assert not compare_output(token, token, mode="float")
    assert compare_output(token, token, mode="tokens")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_deadline_is_rejected(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        run_python(tmp_path, "", timeout=timeout)


def test_invalid_arguments_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="command"):
        run_program("python3", "", cwd=tmp_path)
    with pytest.raises(ValueError, match="command"):
        run_program([], "", cwd=tmp_path)
    with pytest.raises(ValueError, match="output_limit"):
        run_python(tmp_path, "", output_limit=0)
    with pytest.raises(ValueError, match="comparison mode"):
        compare_output("", "", mode="unknown")
    with pytest.raises(ValueError, match="tolerances"):
        compare_output("1", "1", mode="float", atol=float("nan"))


def test_keyboard_interrupt_kills_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import selectors

    pid_file = tmp_path / "pids.txt"
    original_select = selectors.DefaultSelector.select

    def interrupt_after_start(selector: object, timeout: float | None = None) -> object:
        events = original_select(selector, timeout)
        if pid_file.exists():
            raise KeyboardInterrupt
        return events

    monkeypatch.setattr(selectors.DefaultSelector, "select", interrupt_after_start)
    source = (
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])\n"
        "pathlib.Path('pids.txt').write_text(str(os.getpid()) + ' ' + str(child.pid))\n"
        "time.sleep(20)\n"
    )
    with pytest.raises(KeyboardInterrupt):
        run_python(tmp_path, source)
    pids = [int(value) for value in pid_file.read_text().split()]
    try:
        deadline = time.monotonic() + 1
        while any(is_running(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(is_running(pid) for pid in pids)
    finally:
        for pid in pids:
            if is_running(pid):
                os.kill(pid, signal.SIGKILL)
