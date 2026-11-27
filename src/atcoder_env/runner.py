"""Bounded subprocess execution and output comparison for local judging."""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunResult:
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    elapsed_seconds: float
    status: str
    max_rss_kib: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable execution record."""
        return asdict(self)


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop descendants as well as the solution's immediate process."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_program(
    command: Sequence[str],
    input_data: str,
    *,
    cwd: Path,
    timeout: float = 2.0,
    output_limit: int = 1_048_576,
) -> RunResult:
    """Run a program on POSIX with a wall deadline and combined output byte cap.

    Both output pipes share ``output_limit``; a program cannot evade the limit
    by switching between stdout and stderr. Streams are decoded as UTF-8 with
    replacement. This is a local test runner, not an untrusted-code sandbox.
    RSS is unavailable rather than inferred from cumulative child statistics.
    """
    if os.name != "posix":
        raise RuntimeError("The local runner requires Linux, macOS, or WSL; use Docker on Windows.")
    if isinstance(command, (str, bytes)) or not command:
        raise ValueError("command must be a nonempty sequence of argument strings")
    arguments = list(command)
    if any(not isinstance(argument, str) for argument in arguments):
        raise ValueError("command arguments must be strings")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if isinstance(output_limit, bool) or not isinstance(output_limit, int) or output_limit <= 0:
        raise ValueError("output_limit must be a positive integer")

    encoded_input = input_data.encode("utf-8")
    started = time.monotonic()
    deadline = started + timeout
    try:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            shell=False,
            bufsize=0,
        )
    except OSError as error:
        return RunResult(
            command=arguments,
            returncode=None,
            stdout="",
            stderr=f"{type(error).__name__}: {error}\n",
            elapsed_seconds=time.monotonic() - started,
            status="runtime_error",
        )

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    input_bytes = memoryview(encoded_input)
    input_offset = 0
    captured = 0
    forced_status: str | None = None
    cleanup_deadline: float | None = None

    def close_stream(stream: Any) -> None:
        try:
            selector.unregister(stream)
        except (KeyError, ValueError):
            pass
        stream.close()

    def stop() -> None:
        nonlocal cleanup_deadline
        _kill_process_group(process)
        close_stream(process.stdin)
        if cleanup_deadline is None:
            # Escaped descendants must not keep the runner waiting on open pipes.
            cleanup_deadline = time.monotonic() + 1.0

    try:
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        os.set_blocking(process.stdin.fileno(), False)
        if input_bytes:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()

        while True:
            returncode = process.poll()
            now = time.monotonic()
            if returncode is not None and cleanup_deadline is None:
                # A successful parent may still have left background children.
                stop()
            if returncode is not None and not selector.get_map():
                break
            if forced_status is None and returncode is None and now >= deadline:
                forced_status = "timeout"
                stop()
            if cleanup_deadline is not None and now >= cleanup_deadline:
                break
            active_deadline = cleanup_deadline if cleanup_deadline is not None else deadline
            events = selector.select(min(0.05, max(0.0, active_deadline - now)))
            for key, _ in events:
                stream = key.fileobj
                if stream.closed:
                    continue
                if key.data == "stdin":
                    try:
                        count = os.write(
                            stream.fileno(), input_bytes[input_offset : input_offset + 65_536]
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        close_stream(stream)
                        continue
                    input_offset += count
                    if input_offset == len(input_bytes):
                        close_stream(stream)
                    continue
                try:
                    chunk = os.read(stream.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    close_stream(stream)
                    continue
                remaining = output_limit - captured
                accepted = chunk[:remaining]
                output[key.data].extend(accepted)
                captured += len(accepted)
                if len(chunk) > remaining and forced_status is None:
                    forced_status = "output_limit"
                    stop()

        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            forced_status = forced_status or "timeout"
            stop()
    except BaseException:
        stop()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        input_bytes.release()

    status = forced_status or ("ok" if process.returncode == 0 else "runtime_error")
    return RunResult(
        command=arguments,
        returncode=process.returncode,
        stdout=output["stdout"].decode("utf-8", errors="replace"),
        stderr=output["stderr"].decode("utf-8", errors="replace"),
        elapsed_seconds=time.monotonic() - started,
        status=status,
    )


def compare_output(
    actual: str,
    expected: str,
    *,
    mode: str = "tokens",
    atol: float = 1e-9,
    rtol: float = 1e-9,
) -> bool:
    """Compare exact bytes-as-text, whitespace-separated tokens, or finite floats."""
    if mode == "exact":
        return actual == expected
    if mode not in {"tokens", "float"}:
        raise ValueError(f"Unknown comparison mode: {mode}")
    actual_tokens, expected_tokens = actual.split(), expected.split()
    if len(actual_tokens) != len(expected_tokens):
        return False
    if mode == "tokens":
        return actual_tokens == expected_tokens
    if any(not math.isfinite(value) or value < 0 for value in (atol, rtol)):
        raise ValueError("Float comparison tolerances must be finite and nonnegative")
    for actual_token, expected_token in zip(actual_tokens, expected_tokens, strict=True):
        try:
            actual_number, expected_number = float(actual_token), float(expected_token)
        except ValueError:
            if actual_token != expected_token:
                return False
        else:
            if not (math.isfinite(actual_number) and math.isfinite(expected_number)):
                return False
            if not math.isclose(actual_number, expected_number, abs_tol=atol, rel_tol=rtol):
                return False
    return True


def verdict(
    result: RunResult,
    expected: str,
    mode: str = "tokens",
    atol: float = 1e-9,
    rtol: float = 1e-9,
) -> str:
    """Translate execution status and output into a familiar judge verdict."""
    failure = {"timeout": "TLE", "runtime_error": "RE", "output_limit": "OLE"}
    if result.status in failure:
        return failure[result.status]
    if result.status != "ok" or result.returncode != 0:
        return "RE"
    return (
        "AC" if compare_output(result.stdout, expected, mode=mode, atol=atol, rtol=rtol) else "WA"
    )
