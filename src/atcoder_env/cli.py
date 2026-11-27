"""A single entry point for the local competitive-programming workflow."""

from __future__ import annotations

import argparse
import importlib.metadata
import math
import os
import sys
from pathlib import Path

from loguru import logger

from atcoder_env import __version__
from atcoder_env.artifacts import configure_logging
from atcoder_env.config import command_args, load_config, validate_config
from atcoder_env.constraint_model import BENCHMARK_PATTERNS
from atcoder_env.external import (
    download_samples,
    executable,
    generate_templates,
    submit,
    task_parts,
)
from atcoder_env.harness import (
    OracleFailure,
    benchmark,
    promote,
    replay,
    run_input,
    stress,
    test_samples,
)
from atcoder_env.runner import run_program
from atcoder_env.workspace import create_workspace, resolve_workspace


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def sizes_list(value: str) -> list[int]:
    try:
        values = [positive_int(part) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("use comma-separated positive sizes") from error
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("sizes must be unique")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ac", description="Prepare, test, shrink, and measure Python AtCoder solutions."
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="action", required=True)
    new = commands.add_parser(
        "new", help="Create a safe offline workspace with explicit TODO solutions"
    )
    new.add_argument(
        "path", metavar="PATH_OR_URL", help="New problem directory or AtCoder task URL"
    )
    new.add_argument("--directory", "-d", type=Path, help="Workspace directory for a URL target")
    new.add_argument("--template", choices=("array", "tree"), default="array")
    prepare = commands.add_parser(
        "prepare", help="Create a workspace and download public sample cases"
    )
    prepare.add_argument("url")
    prepare.add_argument("--directory", "-d", type=Path)
    prepare.add_argument("--template", choices=("array", "tree"), default="array")
    prepare.add_argument(
        "--no-constraints",
        action="store_true",
        help="Download samples without importing constraints",
    )
    prepare.add_argument(
        "--max-size", type=positive_int, default=30, help="Small-input size cap for stress tests"
    )
    commands.add_parser("doctor", help="Check interpreters and installed integration tools offline")

    def problem_command(name: str, help_text: str, *, run_options: bool = False):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "path",
            nargs="?",
            default=".",
            metavar="PATH_OR_URL",
            help="Problem directory or AtCoder task URL (default: current directory)",
        )
        command.add_argument(
            "--directory", "-d", type=Path, help="Workspace directory for a URL target"
        )
        if run_options:
            command.add_argument(
                "--solution", help="Command override, e.g. 'pypy3 main.py' (no shell)"
            )
            command.add_argument(
                "--timeout", type=positive_float, help="Wall timeout per solution process"
            )
            command.add_argument("--comparison", choices=("tokens", "exact", "float"))
            command.add_argument(
                "--memory-limit",
                type=positive_int,
                metavar="MiB",
                help="Resident memory limit in MiB (bench default: 512)",
            )
        return command

    problem_command("test", "Run all sample and regression input/output pairs", run_options=True)
    search = problem_command(
        "stress", "Compare against a brute-force oracle and shrink a failure", run_options=True
    )
    search.add_argument("--oracle", help="Oracle command override")
    search.add_argument("--oracle-timeout", type=positive_float)
    search.add_argument("--max-examples", type=positive_int)
    search.add_argument("--seed", type=int)
    search.add_argument(
        "--budget",
        type=positive_float,
        default=60.0,
        help="Search wall budget in seconds; a running case may finish after this",
    )
    search.add_argument("--no-shrink", action="store_true")
    search.add_argument(
        "--sequential",
        action="store_true",
        help="Run the oracle and solution sequentially instead of concurrently",
    )
    bench = problem_command(
        "bench", "Measure increasing input sizes; does not check correctness", run_options=True
    )
    bench.add_argument(
        "--sizes",
        type=sizes_list,
        help="Comma-separated sizes (default: official bounds and powers of ten from constraints.toml)",
    )
    bench.add_argument("--repeat", type=positive_int, default=3)
    bench.add_argument("--seed", type=int)
    bench.add_argument(
        "--pattern",
        choices=("all", *BENCHMARK_PATTERNS),
        default="all",
        help="Input distribution (default: all feasible declared patterns)",
    )
    bench.add_argument(
        "--no-profile", action="store_true", help="Disable automatic bottleneck profiling"
    )
    bench.add_argument(
        "--slow-seconds",
        type=positive_float,
        help="Successful-run threshold for diagnostics (default: 80%% of timeout)",
    )
    bench.add_argument(
        "--no-shrink", action="store_true", help="Keep resource cases without Hypothesis reduction"
    )
    bench.add_argument(
        "--shrink-budget",
        type=positive_float,
        default=30.0,
        help="Hypothesis search seconds per pattern and resource symptom (default: 30)",
    )
    bench.add_argument(
        "--profile-timeout",
        type=positive_float,
        default=10.0,
        help="Separate profiling time budget in seconds (default: 10)",
    )
    run = problem_command(
        "run", "Run one input and save stdout, stderr, timing, and status", run_options=True
    )
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--expected", type=Path)
    rerun = problem_command(
        "replay", "Replay a saved failure against the current solution", run_options=True
    )
    rerun.add_argument("artifact", type=Path)
    regression = problem_command(
        "promote", "Add a saved failure to regression tests without overwriting"
    )
    regression.add_argument("artifact", type=Path)
    regression.add_argument("--name", required=True)
    download = problem_command("download", "Download samples into an existing problem workspace")
    download.add_argument("--url")
    constraints = problem_command(
        "constraints", "Import supported input constraints and a Hypothesis generator"
    )
    constraints.add_argument("--url", help="AtCoder task URL (defaults to atcoder.toml)")
    constraints.add_argument("--max-size", type=positive_int, default=30)
    constraints.add_argument("--html", type=Path, help="Read a saved English task page offline")
    problem_command("template", "Generate input-code drafts for manual review via oj-template")
    submission = problem_command(
        "submit", "Submit one source via oj with its interactive confirmation"
    )
    submission.add_argument("--file", default="main.py")
    submission.add_argument(
        "--language", help="AtCoder language ID; explicit IDs avoid interpreter guessing"
    )
    return parser


def doctor() -> int:
    logger.info("Python: {} ({})", sys.version.split()[0], sys.executable)
    if os.name != "posix":
        raise ValueError("The execution runner requires Linux/macOS. Use Docker or WSL on Windows.")
    for package in (
        "atcoder-environment",
        "hypothesis",
        "line-profiler",
        "psutil",
        "cyaron",
        "loguru",
        "online-judge-tools",
        "online-judge-api-client",
        "online-judge-template-generator",
        "sympy",
        "antlr4-python3-runtime",
        "setuptools",
    ):
        logger.info("{}: {}", package, importlib.metadata.version(package))
    for tool in ("oj", "oj-template", "oj-prepare"):
        result = run_program([executable(tool), "--help"], "", cwd=Path.cwd(), timeout=10)
        if result.status != "ok":
            raise ValueError(f"{tool} could not start: {result.stderr}")
        logger.info("{}: starts successfully", tool)
    logger.info(
        "Offline environment is ready. Network login and AtCoder judge compatibility are separate checks."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging_ready = False
    try:
        if args.action == "doctor":
            configure_logging()
            logging_ready = True
            return doctor()
        if args.action == "prepare":
            task_parts(args.url)
            destination, url = resolve_workspace(args.url, directory=args.directory)
            root = create_workspace(destination, url=url, template=args.template)
            configure_logging(root)
            logging_ready = True
            logger.info("Created {}", root)
            config = load_config(root)
            download_samples(config)
            if args.no_constraints or args.template != "array":
                logger.info("Edit cases.py to match this problem's format and constraints.")
                return 0
            from atcoder_env.constraints import import_constraints

            result = import_constraints(config, max_size=args.max_size)
            if result:
                logger.warning(
                    "Samples are ready; automatic constraints are incomplete. "
                    "Review the report and edit cases.py before stress testing."
                )
            return result
        destination, url = resolve_workspace(args.path, directory=args.directory)
        override_url = getattr(args, "url", None)
        if url and override_url and task_parts(url) != task_parts(override_url):
            raise ValueError("The positional task URL and --url must identify the same problem")
        if args.action == "new":
            root = create_workspace(destination, url=url, template=args.template)
            configure_logging(root)
            logging_ready = True
            logger.info("Created {}. Edit main.py, brute.py, and cases.py before testing.", root)
            return 0
        created = False
        if url and (
            not destination.exists() or (destination.is_dir() and not any(destination.iterdir()))
        ):
            if args.action not in {"constraints", "download", "template"}:
                raise ValueError(
                    f"Problem workspace is not prepared: {destination}. Run: ac prepare {url}"
                    + (f" --directory {destination}" if args.directory is not None else "")
                )
            destination = create_workspace(destination, url=url)
            created = True
        config = load_config(destination)
        if url:
            if config.url and task_parts(config.url) != task_parts(url):
                raise ValueError(
                    f"{config.root} is configured for {config.url}, not {url}. "
                    "Select the matching workspace with --directory."
                )
            config.url = url
        configure_logging(config.root)
        logging_ready = True
        if created:
            logger.info("Created {}", config.root)
        for option in ("solution", "oracle"):
            value = getattr(args, option, None)
            if value is not None:
                setattr(config, option, command_args(value))
        for option in ("timeout", "oracle_timeout", "comparison"):
            value = getattr(args, option, None)
            if value is not None:
                setattr(config, option, value)
        if getattr(args, "memory_limit", None) is not None:
            config.memory_limit_mib = args.memory_limit
        validate_config(config)
        if args.action == "test":
            return test_samples(config)
        if args.action == "stress":
            return stress(
                config,
                max_examples=args.max_examples or config.examples,
                seed=args.seed if args.seed is not None else config.seed,
                shrink=not args.no_shrink,
                budget=args.budget,
                parallel=not args.sequential,
            )
        if args.action == "bench":
            return benchmark(
                config,
                sizes=args.sizes,
                repeat=args.repeat,
                seed=args.seed if args.seed is not None else config.seed,
                pattern=args.pattern,
                profile=not args.no_profile,
                profile_timeout=args.profile_timeout,
                slow_seconds=args.slow_seconds,
                memory_limit_mib=config.memory_limit_mib or 512,
                shrink=not args.no_shrink,
                shrink_budget=args.shrink_budget,
            )
        if args.action == "run":
            return run_input(config, args.input, expected_path=args.expected)
        if args.action == "replay":
            return replay(config, args.artifact)
        if args.action == "promote":
            return promote(config, args.artifact, args.name)
        if args.action == "download":
            return download_samples(config, args.url)
        if args.action == "constraints":
            from atcoder_env.constraints import import_constraints

            return import_constraints(
                config, url=args.url, max_size=args.max_size, html_path=args.html
            )
        if args.action == "template":
            return generate_templates(config)
        if args.action == "submit":
            return submit(config, file=args.file, language=args.language)
    except KeyboardInterrupt:
        if not logging_ready:
            configure_logging(Path.home() / ".cache" / "atcoder-environment")
        logger.warning("Interrupted; completed artifacts remain available")
        return 130
    except (OSError, ValueError, OracleFailure, importlib.metadata.PackageNotFoundError) as error:
        if not logging_ready:
            configure_logging(Path.home() / ".cache" / "atcoder-environment")
        logger.error("{}", error)
        return 2
    except Exception:
        if not logging_ready:
            configure_logging(Path.home() / ".cache" / "atcoder-environment")
        logger.exception("Testing harness failed; this is not a successful verification")
        return 2
    return 2
