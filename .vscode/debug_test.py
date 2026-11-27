"""Run the active problem's samples or interactive judge under VS Code's debugger."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import onlinejudge_command.subcommand.test as oj_test
from onlinejudge_command.main import main as oj_main


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("Usage: debug_test.py <task_dir> [oj options...]", file=sys.stderr)
        return

    task_dir = Path(args[0]).resolve()
    if not (task_dir / "main.py").is_file():
        print(f"Error: {task_dir / 'main.py'} not found.", file=sys.stderr)
        return

    python = shlex.quote(sys.executable)
    command = f"{python} main.py"
    if (task_dir / "judge.py").is_file():
        oj_args = ["t/r", "-c", command, f"{python} judge.py", *args[1:]]
    elif (task_dir / "test").is_dir():
        oj_args = ["t", "-c", command, "-d", "test", *args[1:]]
    else:
        print(f"Error: {task_dir / 'test'} directory not found.", file=sys.stderr)
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
