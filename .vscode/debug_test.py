"""Debug the latest ac failure, or the active problem's samples/interactive judge."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import onlinejudge_command.subcommand.test as oj_test
from loguru import logger
from onlinejudge_command.main import main as oj_main

from atcoder_env.artifacts import configure_logging
from atcoder_env.config import load_config
from atcoder_env.debug_artifacts import find_latest_failures


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        logger.error("Usage: debug_test.py <task_dir> [oj options...]")
        return

    task_dir = Path(args[0]).resolve()
    if not task_dir.is_dir():
        logger.error("Problem directory not found: {}", task_dir)
        return
    configure_logging(task_dir)
    config = load_config(task_dir)
    failures = find_latest_failures(task_dir)
    if failures:
        for index, target in enumerate(failures, 1):
            command = config.oracle if target.role == "oracle" else config.solution
            logger.info(
                "Debug failure {}/{}: {} {}, role={}, pattern={}, reduced={}",
                index,
                len(failures),
                target.kind,
                target.verdict,
                target.role,
                target.pattern or "-",
                target.reduced,
            )
            logger.info("Saved input: {}", target.input_path)
            logger.info("Current command: {}", shlex.join(command))
            # A real stdin file preserves input(), sys.stdin.buffer, and fd 0.
            # Launch directly so debugpy can follow the Python subprocess, with
            # no ac time/memory limits to interrupt a paused debugging session.
            with target.input_path.open("rb") as stream:
                result = subprocess.run(command, cwd=task_dir, stdin=stream, check=False)
            logger.info("Debug process exited with code {}", result.returncode)
        return

    logger.info("No saved failure in the latest ac run; debugging tests")
    python = shlex.quote(sys.executable)
    command = shlex.join(config.solution)
    if (task_dir / "judge.py").is_file():
        oj_args = ["t/r", "-c", command, f"{python} judge.py", *args[1:]]
    elif (task_dir / "test").is_dir():
        oj_args = ["t", "-c", command, "-d", "test", *args[1:]]
    else:
        logger.error("Test directory not found: {}", task_dir / "test")
        return

    os.chdir(task_dir)
    # Launch Python directly so debugpy can attach to the solution subprocess.
    # oj's default time limit is infinite, allowing pauses at breakpoints.
    previous = oj_test.check_gnu_time
    oj_test.check_gnu_time = lambda _: False
    try:
        oj_main(oj_args)
    except SystemExit:
        # oj already reports its verdict; do not pause the debugger on its exit.
        pass
    finally:
        oj_test.check_gnu_time = previous


if __name__ == "__main__":
    main()
