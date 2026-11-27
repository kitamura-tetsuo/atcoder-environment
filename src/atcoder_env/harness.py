"""Sample judging, differential search, replay, and scaling measurements."""

from __future__ import annotations

import csv
import importlib.util
import inspect
import json
import random
import re
import shutil
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from uuid import uuid4

from hypothesis import HealthCheck, Phase, find, settings
from hypothesis.errors import NoSuchExample
from loguru import logger

from atcoder_env.artifacts import new_session, save_case, write_json
from atcoder_env.config import Config
from atcoder_env.runner import RunResult, run_program, verdict


class OracleFailure(Exception):
    """An invalid oracle result must never be reported as a solution mismatch."""


class SearchBudgetExceeded(Exception):
    pass


class StopShrinking(Exception):
    pass


@contextmanager
def case_module(root: Path):
    filename = root / "cases.py"
    if not filename.is_file():
        raise ValueError(f"Missing {filename}; create strategy() and serialize(case) first")
    name = "_atcoder_cases_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(name, filename)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root))
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        for function in ("strategy", "serialize"):
            if not callable(getattr(module, function, None)):
                raise ValueError(f"cases.py must define {function}()")
        yield module
    finally:
        sys.modules.pop(name, None)
        sys.path.remove(str(root))


def serialized(module: ModuleType, case: object) -> str:
    result = module.serialize(case)
    if not isinstance(result, str):
        raise ValueError("cases.serialize(case) must return str")
    return result


def execute(config: Config, data: str, *, oracle: bool = False) -> RunResult:
    return run_program(
        config.oracle if oracle else config.solution,
        data,
        cwd=config.root,
        timeout=config.oracle_timeout if oracle else config.timeout,
        output_limit=config.output_limit,
    )


def judge(config: Config, result: RunResult, expected: str) -> str:
    return verdict(result, expected, mode=config.comparison, atol=config.atol, rtol=config.rtol)


def show_failure(result: RunResult, expected: str | None = None) -> None:
    if expected is not None:
        logger.error("Expected (up to 1000 characters): {!r}", expected[:1000])
    logger.error("Actual (up to 1000 characters): {!r}", result.stdout[:1000])
    if result.stderr:
        logger.error("stderr (up to 2000 characters):\n{}", result.stderr[:2000])


def test_samples(config: Config) -> int:
    inputs = sorted((config.root / "test").glob("*.in"))
    if not inputs:
        raise ValueError("No test/*.in files found. Use ac download or add input/output pairs.")
    for filename in inputs:
        if not filename.with_suffix(".out").is_file():
            raise ValueError(f"Expected output missing: {filename.with_suffix('.out')}")
    session = new_session(config, "test")
    results = []
    for index, filename in enumerate(inputs, 1):
        data = filename.read_text(encoding="utf-8")
        expected = filename.with_suffix(".out").read_text(encoding="utf-8")
        result = execute(config, data)
        status = judge(config, result, expected)
        save_case(
            session,
            f"{index:03d}-{filename.stem}",
            input_data=data,
            actual=result,
            expected=expected,
            verdict=status,
            details={"source": str(filename)},
        )
        results.append(
            {"name": filename.stem, "verdict": status, "elapsed_seconds": result.elapsed_seconds}
        )
        logger.info("{}: {} ({:.4f}s)", filename.stem, status, result.elapsed_seconds)
        if status != "AC":
            show_failure(result, expected)
    failures = sum(result["verdict"] != "AC" for result in results)
    write_json(
        session / "summary.json",
        {"passed": len(results) - failures, "failed": failures, "cases": results},
    )
    logger.info("{} / {} passed", len(results) - failures, len(results))
    return int(failures > 0)


def stress(
    config: Config, *, max_examples: int, seed: int, shrink: bool = True, budget: float = 60.0
) -> int:
    session = new_session(config, "stress")
    started = time.monotonic()
    count = 0
    first = None
    latest = None
    target_signature = None
    search_state = "exhausted"

    with case_module(config.root) as module:

        def evaluate(case: object, *, enforce_budget: bool = True):
            nonlocal count, first, latest, target_signature
            if enforce_budget and time.monotonic() - started > budget:
                raise SearchBudgetExceeded
            data = serialized(module, case)
            count += 1
            oracle = execute(config, data, oracle=True)
            if oracle.status != "ok":
                status = "ORACLE_" + judge(config, oracle, "")
                save_case(
                    session,
                    "oracle-error",
                    input_data=data,
                    actual=oracle,
                    oracle=oracle,
                    verdict=status,
                    details={"stage": "oracle", "seed": seed, "evaluation": count},
                )
                raise OracleFailure(f"Oracle failed ({status}); see {session / 'oracle-error'}")
            actual = execute(config, data)
            status = judge(config, actual, oracle.stdout)
            if status == "AC":
                return None
            signature = (status, actual.returncode if status == "RE" else None)
            record = (data, actual, oracle, status)
            if first is None:
                first = record
                latest = record
                target_signature = signature
                save_case(
                    session,
                    "initial",
                    input_data=data,
                    actual=actual,
                    oracle=oracle,
                    expected=oracle.stdout,
                    verdict=status,
                    details={"seed": seed, "evaluation": count},
                )
                logger.warning("Found {} after {} executions; initial input saved", status, count)
            if signature == target_signature:
                if latest is None or (len(data.split()), len(data), data) < (
                    len(latest[0].split()),
                    len(latest[0]),
                    latest[0],
                ):
                    latest = record
                return record
            return None

        def predicate(case: object) -> bool:
            record = evaluate(case)
            if record is not None and record[3] in {"TLE", "OLE"}:
                raise StopShrinking
            return record is not None

        try:
            edges = module.edge_cases() if callable(getattr(module, "edge_cases", None)) else []
            for case in edges:
                if evaluate(case) is not None:
                    break
            if first is not None and (not shrink or first[3] in {"TLE", "OLE"}):
                search_state = "saved_without_shrinking"
            else:
                phases = (Phase.generate, Phase.shrink) if shrink else (Phase.generate,)
                reduced = find(
                    module.strategy(),
                    predicate,
                    random=random.Random(seed),
                    settings=settings(
                        max_examples=max_examples,
                        deadline=None,
                        database=None,
                        phases=phases,
                        suppress_health_check=[HealthCheck.too_slow],
                    ),
                )
                # Re-run the returned example: a flaky program is not a stable counterexample.
                final_record = evaluate(reduced, enforce_budget=False)
                if final_record is None:
                    raise ValueError(
                        "Counterexample did not reproduce; check nondeterminism in solution/oracle"
                    )
                latest = final_record
                search_state = "reduced" if shrink else "saved_without_shrinking"
        except NoSuchExample:
            search_state = "edge_case_only" if first is not None else "exhausted"
        except SearchBudgetExceeded:
            search_state = "budget_exceeded"
            logger.warning("Search time budget reached ({}s)", budget)
        except StopShrinking:
            search_state = "saved_without_shrinking"
        except BaseException as error:
            write_json(
                session / "summary.json",
                {
                    "status": "ERROR",
                    "seed": seed,
                    "evaluations": count,
                    "error": str(error),
                    "initial_failure_saved": first is not None,
                },
            )
            raise

    failure_path = None
    if latest is not None:
        data, actual, oracle, status = latest
        # Budget and explicit-edge failures need the same reproduction check as shrinking.
        checked_oracle = execute(config, data, oracle=True)
        checked_actual = execute(config, data)
        count += 1
        checked_status = judge(config, checked_actual, checked_oracle.stdout)
        if (
            judge(config, checked_oracle, oracle.stdout) != "AC"
            or checked_status != status
            or (status == "RE" and checked_actual.returncode != actual.returncode)
        ):
            save_case(
                session,
                "unstable",
                input_data=data,
                actual=checked_actual,
                oracle=checked_oracle,
                expected=oracle.stdout,
                verdict="UNSTABLE",
                details={"seed": seed, "previous_verdict": status},
            )
            write_json(
                session / "summary.json",
                {
                    "status": "ERROR",
                    "seed": seed,
                    "evaluations": count,
                    "error": "Failure did not reproduce",
                },
            )
            raise ValueError(
                f"Failure did not reproduce consistently; inspect {session / 'unstable'}"
            )
        actual, oracle = checked_actual, checked_oracle
        failure_path = save_case(
            session,
            "failure",
            input_data=data,
            actual=actual,
            oracle=oracle,
            expected=oracle.stdout,
            verdict=status,
            details={"seed": seed, "evaluations": count, "search_state": search_state},
        )
        logger.error("{}: reproducible input saved to {}", status, failure_path)
        show_failure(actual, oracle.stdout)
    complete = search_state != "budget_exceeded"
    outcome = "FAIL" if first is not None else ("PASS" if complete else "INCOMPLETE")
    write_json(
        session / "summary.json",
        {
            "status": outcome,
            "seed": seed,
            "max_examples": max_examples,
            "evaluations": count,
            "search_state": search_state,
            "elapsed_seconds": time.monotonic() - started,
            "failure": str(failure_path) if failure_path else None,
        },
    )
    logger.info("{}: {} evaluations, seed={}, search={}", outcome, count, seed, search_state)
    return 1 if first is not None else (0 if complete else 2)


def run_input(config: Config, input_path: Path, *, expected_path: Path | None = None) -> int:
    data = input_path.read_text(encoding="utf-8")
    expected = expected_path.read_text(encoding="utf-8") if expected_path else None
    session = new_session(config, "run")
    result = execute(config, data)
    status = (
        judge(config, result, expected)
        if expected is not None
        else {
            "ok": "OK",
            "timeout": "TLE",
            "runtime_error": "RE",
            "output_limit": "OLE",
        }[result.status]
    )
    save_case(session, "case", input_data=data, actual=result, expected=expected, verdict=status)
    write_json(
        session / "summary.json", {"status": status, "elapsed_seconds": result.elapsed_seconds}
    )
    logger.info("{} ({:.4f}s)", status, result.elapsed_seconds)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if status not in {"AC", "OK"}:
        show_failure(result, expected)
    return int(status not in {"AC", "OK"})


def replay(config: Config, artifact: Path) -> int:
    artifact = artifact.resolve()
    if artifact.is_dir():
        input_path = artifact / "input.txt"
        expected_path = artifact / "expected.txt"
    else:
        input_path = artifact
        expected_path = artifact.with_suffix(".out")
    if not expected_path.is_file():
        raise ValueError(f"Cannot replay a comparison without expected output: {expected_path}")
    return run_input(config, input_path, expected_path=expected_path)


def promote(config: Config, artifact: Path, name: str) -> int:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError(
            "Regression name must contain only letters, digits, underscores, and hyphens"
        )
    source_input, source_output = artifact / "input.txt", artifact / "expected.txt"
    if not source_input.is_file() or not source_output.is_file():
        raise ValueError("Artifact must contain input.txt and expected.txt")
    folder = config.root / "test"
    folder.mkdir(exist_ok=True)
    target_input, target_output = folder / f"{name}.in", folder / f"{name}.out"
    if target_input.exists() or target_output.exists():
        raise ValueError(f"Regression test already exists: {name}")
    for source, destination in ((source_input, target_input), (source_output, target_output)):
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(source.read_text(encoding="utf-8"))
    logger.info("Saved regression test: {}", target_input)
    return 0


def _profile_command(config: Config) -> list[str]:
    command = config.solution
    interpreter = shutil.which(command[0])
    if (
        interpreter is None
        or Path(interpreter).resolve() != Path(sys.executable).resolve()
        or len(command) < 2
        or command[1].startswith("-")
        or not command[1].endswith(".py")
        or not (config.root / command[1]).is_file()
    ):
        raise ValueError("--profile requires a direct Python script using the ac interpreter")
    return command[1:]


def _profile_benchmark(
    config: Config, session: Path, size: int, data: str, timeout: float, script: list[str]
) -> str:
    name = f"size-{size}-profile"
    target = session / name
    target.mkdir()
    result = run_program(
        [
            sys.executable,
            "-m",
            "atcoder_env.line_profile",
            str(target),
            str(timeout),
            *script,
        ],
        data,
        cwd=config.root,
        timeout=timeout + 2.0,
        output_limit=config.output_limit,
    )
    report = target / "profile-status.json"
    status = result.status
    if report.is_file():
        status = json.loads(report.read_text(encoding="utf-8"))["status"]
        if status == "ok" and result.status != "ok":
            status = result.status
    elif status == "ok":
        status = "missing_report"
    save_case(session, name, input_data=data, actual=result, verdict=status)
    csv_path = target / "line-profile.csv"
    if csv_path.is_file():
        logger.info("n={}: line profile ({}) {}", size, status, csv_path)
        with csv_path.open(encoding="utf-8", newline="") as handle:
            for row in list(csv.DictReader(handle))[:5]:
                logger.info(
                    "  {}:{} {} hits={} time={:.6f}s | {}",
                    Path(row["file"]).name,
                    row["line"],
                    row["function"],
                    row["hits"],
                    float(row["seconds"]),
                    row["source"].strip(),
                )
    if status != "ok":
        logger.warning("n={}: profiling {} (separate from benchmark verdict)", size, status)
    return status


def benchmark(
    config: Config,
    *,
    sizes: list[int],
    repeat: int,
    seed: int,
    pattern: str = "default",
    profile: bool = False,
    profile_timeout: float = 10.0,
) -> int:
    script = _profile_command(config) if profile else None
    session = new_session(config, "bench")
    rows = []
    failed = False
    profile_failed = False
    with case_module(config.root) as module:
        generator = getattr(module, "benchmark_case", None)
        if not callable(generator):
            raise ValueError("cases.py must define benchmark_case(size, seed)")
        if pattern != "default" and "pattern" not in inspect.signature(generator).parameters:
            raise ValueError("This cases.py does not support --pattern; add a pattern parameter")
        for size in sizes:
            case = (
                generator(size, seed)
                if pattern == "default"
                else generator(size, seed, pattern=pattern)
            )
            data = serialized(module, case)
            results = []
            for attempt in range(repeat):
                result = execute(config, data)
                results.append(result)
                status = "OK" if result.status == "ok" else judge(config, result, "")
                save_case(
                    session,
                    f"size-{size}-run-{attempt + 1}",
                    input_data=data,
                    actual=result,
                    verdict=status,
                    details={"size": size, "seed": seed, "pattern": pattern},
                )
                if result.status != "ok":
                    failed = True
                    break
            times = [result.elapsed_seconds for result in results]
            row = {
                "size": size,
                "runs": len(results),
                "median_seconds": statistics.median(times),
                "min_seconds": min(times),
                "max_seconds": max(times),
                "status": results[-1].status,
                "input_bytes": len(data.encode("utf-8")),
                "pattern": pattern,
            }
            rows.append(row)
            logger.info(
                "n={}: median={:.4f}s, status={}", size, row["median_seconds"], row["status"]
            )
            if script is not None:
                row["profile_status"] = _profile_benchmark(
                    config,
                    session,
                    size,
                    data,
                    profile_timeout,
                    script,
                )
                profile_failed |= row["profile_status"] != "ok"
            if failed:
                logger.warning("Stopping at failed size; larger sizes were not executed")
                break
    with (session / "benchmark.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_json(
        session / "summary.json",
        {
            "seed": seed,
            "rows": rows,
            "checks_correctness": False,
            "pattern": pattern,
            "profile": profile,
            "profile_timeout": profile_timeout if profile else None,
        },
    )
    logger.info("Measurements: {}", session / "benchmark.csv")
    return 1 if failed else 2 if profile_failed else 0
