"""Select saved failures from only the active problem's newest execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from loguru import logger

_RUN_NAME = re.compile(r"\d{8}T\d{6}\.\d{6}Z-(?P<kind>[a-z]+)-[0-9a-f]{6}\Z")
_MEASUREMENT_NAME = re.compile(
    r"size-(?P<size>[1-9]\d*)(?:-(?P<pattern>[A-Za-z][A-Za-z0-9_-]*))?"
    r"-run-(?P<attempt>[1-9]\d*)\Z"
)
_DIAGNOSTIC_NAME = re.compile(
    r"size-(?P<size>[1-9]\d*)(?:-(?P<pattern>[A-Za-z][A-Za-z0-9_-]*))?"
    r"-(?P<reason>timeout|memory_limit|slow)\Z"
)
_FAILURE_STATUS = {
    "WA": "ok",
    "RE": "runtime_error",
    "TLE": "timeout",
    "MLE": "memory_limit",
    "OLE": "output_limit",
}
_RESOURCE_VERDICT = {
    "timeout": "TLE",
    "memory_limit": "MLE",
    "output_limit": "OLE",
    "runtime_error": "RE",
}
_MAX_METADATA_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class FailureTarget:
    input_path: Path
    session: Path
    kind: str
    verdict: str
    role: str = "solution"
    pattern: str | None = None
    size: int | None = None
    reduced: bool = False


def _safe_file(session: Path, path: Path) -> bool:
    """Accept only regular files reached without following artifact symlinks."""
    try:
        relative = path.relative_to(session)
        current = session
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                logger.warning("Ignoring symbolic-link debug artifact: {}", current)
                return False
        return path.is_file() and path.resolve().is_relative_to(session.resolve())
    except (OSError, ValueError) as error:
        logger.warning("Cannot inspect debug artifact {}: {}", path, error)
        return False


def _read_object(session: Path, path: Path) -> dict[str, Any] | None:
    if not _safe_file(session, path):
        logger.warning("Missing or unusable debug metadata: {}", path)
        return None
    try:
        if path.stat().st_size > _MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds the 16 MiB limit")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("metadata must contain a JSON object")
        return value
    except (OSError, ValueError, RecursionError) as error:
        logger.warning("Cannot read debug metadata {}: {}", path, error)
        return None


def _failure(
    session: Path,
    folder: Path,
    kind: str,
    *,
    role: str = "solution",
    pattern: str | None = None,
    size: int | None = None,
    record: dict[str, Any] | None = None,
) -> FailureTarget | None:
    if not folder.exists():
        return None
    record = _read_object(session, folder / "result.json") if record is None else record
    if record is None:
        return None
    saved_verdict = record.get("verdict")
    if not isinstance(saved_verdict, str):
        logger.warning("Missing failure verdict in {}", folder / "result.json")
        return None
    verdict = saved_verdict.removeprefix("ORACLE_") if role == "oracle" else saved_verdict
    verdict = _RESOURCE_VERDICT.get(verdict, verdict)
    if verdict not in _FAILURE_STATUS:
        if verdict not in {"AC", "OK", "ok", "slow", "UNSTABLE"}:
            logger.warning("Unknown failure verdict {!r} in {}", saved_verdict, folder)
        return None
    execution = record.get("solution")
    if not isinstance(execution, dict) or execution.get("status") != _FAILURE_STATUS[verdict]:
        logger.warning("Failure verdict does not match ordinary execution in {}", folder)
        return None
    if role == "oracle" and not saved_verdict.startswith("ORACLE_"):
        logger.warning("Missing oracle failure verdict in {}", folder)
        return None
    if pattern is not None and record.get("pattern", pattern) != pattern:
        logger.warning("Benchmark pattern does not match artifact directory: {}", folder)
        return None
    input_path = folder / "input.txt"
    if not _safe_file(session, input_path):
        logger.warning("Missing or unusable failure input: {}", input_path)
        return None
    return FailureTarget(
        input_path=input_path,
        session=session,
        kind=kind,
        verdict=f"ORACLE_{verdict}" if role == "oracle" else verdict,
        role=role,
        pattern=pattern,
        size=size,
    )


def _with_reduction(target: FailureTarget, initial: Path) -> FailureTarget:
    if _safe_file(target.session, initial):
        try:
            return replace(
                target, reduced=target.input_path.stat().st_size < initial.stat().st_size
            )
        except OSError as error:
            logger.warning("Cannot compare saved debug inputs: {}", error)
    return target


def _stress_failures(session: Path, summary: dict[str, Any] | None) -> list[FailureTarget]:
    if summary is not None:
        status = summary.get("status")
        if not isinstance(status, str):
            logger.warning("Unrecognized stress summary in {}", session)
            return []
        if status in {"PASS", "INCOMPLETE"}:
            return []
        if status not in {"FAIL", "ERROR"}:
            logger.warning("Unrecognized stress summary in {}", session)
            return []
    if summary is None or summary.get("status") == "ERROR":
        oracle = _failure(session, session / "oracle-error", "stress", role="oracle")
        if oracle is not None:
            return [oracle]
    failure = _failure(session, session / "failure", "stress")
    if failure is not None:
        return [_with_reduction(failure, session / "initial" / "input.txt")]
    initial = _failure(session, session / "initial", "stress")
    return [initial] if initial is not None else []


def _bench_failures(session: Path, summary: dict[str, Any] | None) -> list[FailureTarget]:
    if summary is not None:
        rows = summary.get("rows")
        # Early benchmark versions wrote summaries only after completing and
        # did not yet include a completion flag.
        completed = summary.get("completed", True)
        if not isinstance(rows, list) or type(completed) is not bool:
            logger.warning("Unrecognized benchmark summary in {}", session)
            return []
        if any(not isinstance(row, dict) or not isinstance(row.get("status"), str) for row in rows):
            logger.warning("Invalid benchmark result rows in {}", session)
            return []
        if completed and all(row["status"] == "ok" for row in rows):
            return []
    candidates: dict[str, tuple[tuple[int, int, int, str], FailureTarget]] = {}

    def add(target: FailureTarget | None, priority: int, attempt: int = 0) -> None:
        if target is None:
            return
        assert target.pattern is not None and target.size is not None
        rank = (priority, target.size, attempt, str(target.input_path))
        current = candidates.get(target.pattern)
        if current is None or rank < current[0]:
            candidates[target.pattern] = (rank, target)

    # Only documented direct measurement folders and two fixed diagnostic levels
    # are inspected. Never glob recursively into profile runs or source snapshots.
    for folder in sorted(session.iterdir()):
        measurement = _MEASUREMENT_NAME.fullmatch(folder.name)
        diagnostic = _DIAGNOSTIC_NAME.fullmatch(folder.name)
        match = measurement or diagnostic
        if match is None:
            continue
        pattern = match["pattern"] or "default"
        size = int(match["size"])
        if measurement is not None:
            add(
                _failure(session, folder, "bench", pattern=pattern, size=size),
                1,
                int(measurement["attempt"]),
            )
            continue
        assert diagnostic is not None
        if diagnostic["reason"] == "slow":
            continue
        final = folder / "failure"
        if final.exists():
            record = _read_object(session, final / "result.json")
            if record is not None and record.get("confirmed") is True:
                target = _failure(
                    session, final, "bench", pattern=pattern, size=size, record=record
                )
                if target is not None:
                    add(_with_reduction(target, folder / "initial" / "input.txt"), 0)
        add(_failure(session, folder / "initial", "bench", pattern=pattern, size=size), 2)
    return sorted(
        (target for _rank, target in candidates.values()),
        key=lambda target: (target.size, target.pattern),
    )


def find_latest_failures(root: Path) -> list[FailureTarget]:
    """Select all failed patterns from the newest run, or allow sample fallback.

    A newer test, replay, successful search, or clean benchmark supersedes older
    failures. Interrupted runs may use their already saved inputs. Metadata paths
    are never followed; all candidates have fixed locations inside that one run.
    """
    root = root.resolve()
    runs = root / ".ac" / "runs"
    try:
        if not runs.exists():
            return []
        if runs.is_symlink() or not runs.resolve().is_relative_to(root):
            logger.warning("Ignoring debug history outside the problem: {}", runs)
            return []
        sessions = [
            path
            for path in runs.iterdir()
            if _RUN_NAME.fullmatch(path.name) and (path.is_dir() or path.is_symlink())
        ]
        if not sessions:
            return []
        session = max(sessions, key=lambda path: path.name)
        if session.is_symlink() or not session.resolve().is_relative_to(runs.resolve()):
            logger.warning("Ignoring symbolic-link debug session: {}", session)
            return []
        match = _RUN_NAME.fullmatch(session.name)
        assert match is not None
        kind = match["kind"]
        if kind not in {"stress", "bench"}:
            return []
        summary_path = session / "summary.json"
        summary = None
        if summary_path.exists() or summary_path.is_symlink():
            summary = _read_object(session, summary_path)
            if summary is None:
                return []
        return (
            _stress_failures(session, summary)
            if kind == "stress"
            else _bench_failures(session, summary)
        )
    except (OSError, ValueError) as error:
        logger.warning("Cannot select the newest failure in {}: {}", runs, error)
        return []
