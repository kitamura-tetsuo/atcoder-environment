"""Exercise input distributions and diagnose reproducible resource bottlenecks."""

from __future__ import annotations

import csv
import hashlib
import inspect
import json
import math
import re
import shutil
import statistics
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

from loguru import logger

from atcoder_env.artifacts import new_session, save_case, write_json
from atcoder_env.bench_minimize import minimize_resource_case
from atcoder_env.benchmark_sizes import choose_benchmark_sizes
from atcoder_env.config import Config
from atcoder_env.runner import RunResult, run_program, verdict
from atcoder_env.workspace import require_configured_generator


@dataclass
class _Witness:
    name: str
    pattern: str
    size: int
    case: Any
    data: str
    runs: list[RunResult]
    reason: str
    row: dict


def _hook(module: ModuleType, generator: Any, name: str):
    # Existing generated cases.py files already expose bound model methods.
    return getattr(module, name, None) or getattr(getattr(generator, "__self__", None), name, None)


def _profile_command(config: Config) -> list[str] | None:
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
        return None
    return command[1:]


def _profile_input(
    config: Config, target: Path, data: str, timeout: float, memory_limit_mib: int
) -> str:
    script = _profile_command(config)
    if script is None:
        logger.warning(
            "Automatic line profiling requires a direct Python script using the ac interpreter; "
            "the ordinary measurements and input are saved"
        )
        return "unavailable"
    target.mkdir(parents=True, exist_ok=True)
    result = run_program(
        [
            sys.executable,
            "-m",
            "atcoder_env.line_profile",
            str(target),
            str(timeout),
            str(memory_limit_mib),
            *script,
        ],
        data,
        cwd=config.root,
        timeout=timeout + 2.0,
        output_limit=config.output_limit,
        # Let the profiler's own monitor interrupt and flush before the outer guard.
        memory_limit_mib=math.ceil(memory_limit_mib * 1.5),
    )
    report = target / "profile-status.json"
    status = result.status
    if report.is_file():
        status = json.loads(report.read_text(encoding="utf-8"))["status"]
        if status == "ok" and result.status != "ok":
            status = result.status
    elif status == "ok":
        status = "missing_report"
    save_case(target.parent, target.name, input_data=data, actual=result, verdict=status)
    csv_path = target / "line-profile.csv"
    if csv_path.is_file():
        logger.info("Automatic line profile ({}) {}", status, csv_path)
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
        logger.warning("Profiling {} (separate from ordinary benchmark measurements)", status)
    return status


def _diagnose(
    config: Config,
    session: Path,
    witness: _Witness,
    strategy_factory: Any,
    serialize: Any,
    run: Any,
    *,
    seed: int,
    slow_seconds: float,
    memory_limit_mib: int,
    shrink: bool,
    shrink_budget: float,
    profile: bool,
    profile_timeout: float,
    diagnostic: dict,
) -> None:
    target = session / witness.name
    details = {
        "resource_reason": witness.reason,
        "pattern": witness.pattern,
        "original_size": witness.size,
        "seed": seed,
        "slow_seconds": slow_seconds,
        "timeout": config.timeout,
        "memory_limit_mib": memory_limit_mib,
        "checks_correctness": False,
    }
    save_case(
        target,
        "initial",
        input_data=witness.data,
        actual=witness.runs[-1],
        verdict=witness.reason,
        details={**details, "confirmed": False},
    )
    diagnostic.update(details)
    diagnostic.update(
        {"directory": witness.name, "initial_input_bytes": len(witness.data.encode("utf-8"))}
    )
    data = witness.data
    final_runs = witness.runs

    def preserve(case: Any, input_data: str, runs: tuple[RunResult, ...]) -> None:
        save_case(
            target,
            "failure",
            input_data=input_data,
            actual=runs[-1],
            verdict=witness.reason,
            details={
                **details,
                "confirmed": True,
                "confirmation_runs": [r.to_dict() for r in runs],
            },
        )
        diagnostic.update(
            {
                "state": "searching",
                "confirmed": True,
                "input_bytes": len(input_data.encode("utf-8")),
                "failure_path": str(target / "failure" / "input.txt"),
            }
        )
        write_json(target / "minimization.json", diagnostic)

    if shrink and callable(strategy_factory):
        logger.info(
            "Hypothesis: pattern={}, reason={}, n={}, budget={}s",
            witness.pattern,
            witness.reason,
            witness.size,
            shrink_budget,
        )
        strategy = strategy_factory(witness.size, seed, pattern=witness.pattern)
        preliminary = None
        if "size_only" in inspect.signature(strategy_factory).parameters:
            preliminary = strategy_factory(
                witness.size, seed, pattern=witness.pattern, size_only=True
            )
        reduction = minimize_resource_case(
            witness.case,
            strategy=strategy,
            preliminary_strategy=preliminary,
            serialize=serialize,
            run=run,
            reason=witness.reason,
            slow_seconds=slow_seconds,
            seed=seed,
            budget=shrink_budget,
            initial_runs=witness.runs,
            on_improvement=preserve,
        )
        data, final_runs = reduction.input_data, list(reduction.runs)
        diagnostic.update(
            {
                "state": reduction.state,
                "confirmed": reduction.confirmed,
                "evaluations": reduction.evaluations,
                "candidates": reduction.candidates,
                "elapsed_seconds": reduction.elapsed_seconds,
                "flaky_candidates": reduction.flaky_candidates,
            }
        )
    else:
        diagnostic.update(
            {"state": "disabled" if not shrink else "unavailable", "confirmed": False}
        )
        if shrink:
            logger.warning(
                "No benchmark_strategy(max_size, seed, pattern=...) hook; "
                "keeping the {} input without claiming it was minimized",
                witness.pattern,
            )
    save_case(
        target,
        "failure",
        input_data=data,
        actual=final_runs[-1],
        verdict=witness.reason,
        details={
            **details,
            "confirmed": diagnostic["confirmed"],
            "minimization_state": diagnostic["state"],
            "confirmation_runs": [r.to_dict() for r in final_runs],
        },
    )
    diagnostic["failure_path"] = str(target / "failure" / "input.txt")
    diagnostic["input_bytes"] = len(data.encode("utf-8"))
    witness.row["minimize_state"] = diagnostic["state"]
    logger.info(
        "{} {}: {} ({} -> {} bytes, confirmed={}); input: {}",
        witness.pattern,
        witness.reason,
        diagnostic["state"],
        diagnostic["initial_input_bytes"],
        diagnostic["input_bytes"],
        diagnostic["confirmed"],
        diagnostic["failure_path"],
    )
    write_json(target / "minimization.json", diagnostic)
    if profile:
        diagnostic["profile_status"] = _profile_input(
            config, target / "profile", data, profile_timeout, memory_limit_mib
        )
        profile_path = target / "profile" / "line-profile.csv"
        diagnostic["profile_path"] = str(profile_path) if profile_path.is_file() else ""
        witness.row["profile_status"] = diagnostic["profile_status"]
        witness.row["profile_path"] = diagnostic["profile_path"]
    write_json(target / "minimization.json", diagnostic)


def benchmark(
    config: Config,
    *,
    sizes: list[int] | None = None,
    repeat: int,
    seed: int,
    pattern: str = "all",
    profile: bool = True,
    profile_timeout: float = 10.0,
    slow_seconds: float | None = None,
    memory_limit_mib: int = 512,
    shrink: bool = True,
    shrink_budget: float = 30.0,
) -> int:
    """Measure all supported distributions, then shrink and profile bottlenecks.

    A failed family stops at that size; other families continue independently.
    Each family's first input for each resource symptom is diagnosed. Runtime
    measurements, resource predicates, and profiling always use separate runs.
    """
    from atcoder_env.harness import case_module, serialized

    slow_seconds = 0.8 * config.timeout if slow_seconds is None else slow_seconds
    if not math.isfinite(slow_seconds) or slow_seconds <= 0:
        raise ValueError("slow_seconds must be a positive finite number")
    if type(repeat) is not int or repeat <= 0:
        raise ValueError("repeat must be a positive integer")
    if type(memory_limit_mib) is not int or memory_limit_mib <= 0:
        raise ValueError("memory_limit_mib must be a positive integer")
    for value in (profile_timeout, shrink_budget):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Profiling and shrinking budgets must be positive finite numbers")
    source = (
        "--sizes"
        if sizes is not None
        else ("constraints.toml" if (config.root / "constraints.toml").exists() else "fallback")
    )
    sizes = choose_benchmark_sizes(config.root, sizes)
    logger.info("Benchmark sizes ({}): {}", source, ", ".join(map(str, sizes)))
    logger.info(
        "Automatic diagnostics: slow >= {:.3f}s, timeout={}s, memory limit={} MiB",
        slow_seconds,
        config.timeout,
        memory_limit_mib,
    )
    if source == "fallback":
        logger.info("No constraints.toml; use --sizes to cover this problem's official bounds")
    require_configured_generator(config.root / "cases.py")
    session = new_session(replace(config, memory_limit_mib=memory_limit_mib), "bench")
    rows: list[dict] = []
    skipped: list[dict] = []
    witnesses: dict[tuple[str, str], _Witness] = {}
    diagnostics: list[dict] = []
    blocked: dict[str, int] = {}
    failed = False
    completed = False
    coverage = "custom_default"

    def execute(data: str) -> RunResult:
        return run_program(
            config.solution,
            data,
            cwd=config.root,
            timeout=config.timeout,
            output_limit=config.output_limit,
            memory_limit_mib=memory_limit_mib,
        )

    try:
        with case_module(config.root) as module:
            generator = getattr(module, "benchmark_case", None)
            if not callable(generator):
                raise ValueError("cases.py must define benchmark_case(size, seed)")
            supports_pattern = "pattern" in inspect.signature(generator).parameters
            patterns_factory = _hook(module, generator, "benchmark_patterns")
            strategy_factory = _hook(module, generator, "benchmark_strategy")
            if pattern != "all" and pattern != "default" and not supports_pattern:
                raise ValueError(
                    "This cases.py does not support --pattern; add a pattern parameter"
                )
            if pattern == "all" and not callable(patterns_factory):
                logger.warning(
                    "This cases.py exposes only its default distribution. "
                    "Add benchmark_patterns(size) and benchmark_strategy(max_size, seed, pattern=...) "
                    "for distribution coverage and resource shrinking."
                )
            elif callable(patterns_factory):
                coverage = "declared_patterns"
            for size in sizes:
                patterns = (
                    list(patterns_factory(size))
                    if pattern == "all" and callable(patterns_factory)
                    else ["default" if pattern == "all" else pattern]
                )
                if not patterns or any(
                    not isinstance(item, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", item)
                    for item in patterns
                ):
                    raise ValueError(
                        "benchmark_patterns(size) must return nonempty safe pattern names"
                    )
                if len(set(patterns)) != len(patterns):
                    raise ValueError("benchmark_patterns(size) must not repeat pattern names")
                seen: dict[bytes, dict] = {}
                for family in patterns:
                    if family in blocked and size >= blocked[family]:
                        skipped.append(
                            {"size": size, "pattern": family, "reason": "earlier_failure"}
                        )
                        continue
                    if family != "default" and not supports_pattern:
                        raise ValueError(
                            "This cases.py does not support --pattern; add a pattern parameter"
                        )
                    case = (
                        generator(size, seed)
                        if family == "default"
                        else generator(size, seed, pattern=family)
                    )
                    data = serialized(module, case)
                    digest = hashlib.sha256(data.encode("utf-8")).digest()
                    prefix = f"size-{size}" + ("" if family == "default" else f"-{family}")
                    if digest in seen:
                        original = seen[digest]
                        rows.append(
                            {
                                **original,
                                "pattern": family,
                                "runs": 0,
                                "duplicate_of": original["pattern"],
                            }
                        )
                        if original["status"] != "ok":
                            blocked[family] = min(size, blocked.get(family, size))
                        continue
                    results = []
                    for attempt in range(repeat):
                        result = execute(data)
                        results.append(result)
                        save_case(
                            session,
                            f"{prefix}-run-{attempt + 1}",
                            input_data=data,
                            actual=result,
                            verdict="OK" if result.status == "ok" else verdict(result, ""),
                            details={
                                "size": size,
                                "seed": seed,
                                "pattern": family,
                                "memory_limit_mib": memory_limit_mib,
                            },
                        )
                        if result.status != "ok":
                            failed = True
                            blocked[family] = min(size, blocked.get(family, size))
                            break
                    times = [result.elapsed_seconds for result in results]
                    peaks = [
                        result.max_rss_kib for result in results if result.max_rss_kib is not None
                    ]
                    row = {
                        "size": size,
                        "pattern": family,
                        "runs": len(results),
                        "median_seconds": statistics.median(times),
                        "min_seconds": min(times),
                        "max_seconds": max(times),
                        "status": results[-1].status,
                        "max_rss_kib": max(peaks) if peaks else None,
                        "input_bytes": len(data.encode("utf-8")),
                        "duplicate_of": "",
                        "diagnostic": "",
                        "minimize_state": "not_needed",
                        "profile_status": "not_needed",
                        "profile_path": "",
                    }
                    rows.append(row)
                    seen[digest] = row
                    logger.info(
                        "n={}, pattern={}: median={:.4f}s, peak_rss={}, status={}",
                        size,
                        family,
                        row["median_seconds"],
                        f"{row['max_rss_kib'] / 1024:.1f} MiB"
                        if row["max_rss_kib"] is not None
                        else "unknown",
                        row["status"],
                    )
                    reason = row["status"] if row["status"] in {"timeout", "memory_limit"} else None
                    if row["status"] == "ok" and row["median_seconds"] >= slow_seconds:
                        reason = "slow"
                    if reason is not None:
                        key = (family, reason)
                        if key not in witnesses:
                            name = f"{prefix}-{reason}"
                            witnesses[key] = _Witness(
                                name, family, size, case, data, results, reason, row
                            )
                            # Save before any subsequent measurement, reduction, or interruption.
                            save_case(
                                session / name,
                                "initial",
                                input_data=data,
                                actual=results[-1],
                                verdict=reason,
                                details={
                                    "size": size,
                                    "seed": seed,
                                    "pattern": family,
                                    "resource_reason": reason,
                                    "memory_limit_mib": memory_limit_mib,
                                    "slow_seconds": slow_seconds,
                                },
                            )
                        row["diagnostic"] = witnesses[key].name
                        row["minimize_state"] = "pending" if shrink else "disabled"
                        row["profile_status"] = "pending" if profile else "disabled"
                    if row["status"] != "ok":
                        logger.warning(
                            "Skipping larger sizes for {}; other distributions continue", family
                        )
            # Diagnose after the matrix so reduction work cannot affect coverage order.
            for witness in witnesses.values():
                diagnostic: dict = {"state": "pending", "directory": witness.name}
                diagnostics.append(diagnostic)
                _diagnose(
                    config,
                    session,
                    witness,
                    strategy_factory,
                    lambda case: serialized(module, case),
                    execute,
                    seed=seed,
                    slow_seconds=slow_seconds,
                    memory_limit_mib=memory_limit_mib,
                    shrink=shrink,
                    shrink_budget=shrink_budget,
                    profile=profile,
                    profile_timeout=profile_timeout,
                    diagnostic=diagnostic,
                )
        completed = True
    finally:
        by_directory = {item["directory"]: item for item in diagnostics}
        for row in rows:
            if diagnostic := by_directory.get(row["diagnostic"]):
                row["minimize_state"] = diagnostic["state"]
                row["profile_status"] = diagnostic.get(
                    "profile_status", "pending" if profile else "disabled"
                )
                row["profile_path"] = diagnostic.get("profile_path", "")
        if rows:
            with (session / "benchmark.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        write_json(
            session / "summary.json",
            {
                "completed": completed,
                "seed": seed,
                "sizes": sizes,
                "sizes_source": source,
                "rows": rows,
                "skipped": skipped,
                "checks_correctness": False,
                "pattern": pattern,
                "coverage": coverage,
                "memory_limit_mib": memory_limit_mib,
                "slow_seconds": slow_seconds,
                "profile": "auto" if profile else "disabled",
                "profile_timeout": profile_timeout,
                "shrink": shrink,
                "shrink_budget": shrink_budget,
                "diagnostics": diagnostics,
            },
        )
        logger.info("Measurements: {}", session / "benchmark.csv")
    incomplete = any(
        diagnostic.get("state") == "unstable"
        or diagnostic.get("profile_status", "ok") not in {"ok", "unavailable"}
        for diagnostic in diagnostics
    )
    return 1 if failed else 2 if incomplete else 0
