"""Read explicit, shell-free commands from a problem workspace."""

from __future__ import annotations

import math
import shlex
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    root: Path
    url: str | None = None
    solution: list[str] = field(default_factory=lambda: [sys.executable, "main.py"])
    oracle: list[str] = field(default_factory=lambda: [sys.executable, "brute.py"])
    timeout: float = 2.0
    oracle_timeout: float = 30.0
    output_limit: int = 1_048_576
    memory_limit_mib: int | None = None
    oracle_memory_limit_mib: int | None = None
    comparison: str = "tokens"
    atol: float = 1e-9
    rtol: float = 1e-9
    examples: int = 200
    seed: int = 0


def stress_memory_limits(config: Config) -> tuple[int, int]:
    """Resolve the solution cap and the more generous oracle cap."""
    solution = config.memory_limit_mib or 512
    oracle = config.oracle_memory_limit_mib or 4 * solution
    return solution, oracle


def command_args(value: str | list[str]) -> list[str]:
    args = shlex.split(value) if isinstance(value, str) else value
    if not isinstance(args, list) or not args or any(not isinstance(x, str) or not x for x in args):
        raise ValueError("A command must be a nonempty string or array of strings")
    args = list(args)
    if args[0] in {"python", "python3"}:
        args[0] = sys.executable
    return args


def load_config(path: Path) -> Config:
    root = path.resolve()
    if not root.is_dir():
        raise ValueError(f"Problem directory does not exist: {root}")
    config = Config(root=root)
    file = root / "atcoder.toml"
    if file.exists():
        data = tomllib.loads(file.read_text(encoding="utf-8"))
        allowed = {
            "problem": {"url"},
            "run": {
                "solution",
                "oracle",
                "timeout",
                "oracle_timeout",
                "output_limit",
                "memory_limit_mib",
                "oracle_memory_limit_mib",
                "comparison",
                "atol",
                "rtol",
            },
            "stress": {"examples", "seed"},
        }
        for section, entries in data.items():
            if section not in allowed or not isinstance(entries, dict):
                raise ValueError(f"Unknown configuration section: {section}")
            unknown = entries.keys() - allowed[section]
            if unknown:
                raise ValueError(f"Unknown {section} settings: {', '.join(sorted(unknown))}")
            for key, value in entries.items():
                setattr(config, key, value)
    config.solution = command_args(config.solution)
    config.oracle = command_args(config.oracle)
    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    for name in ("timeout", "oracle_timeout"):
        value = getattr(config, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be a positive finite number")
    for name in ("output_limit", "examples"):
        value = getattr(config, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    for name in ("memory_limit_mib", "oracle_memory_limit_mib"):
        value = getattr(config, name)
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f"{name} must be a positive integer")
    if type(config.seed) is not int:
        raise ValueError("seed must be an integer")
    if config.comparison not in {"tokens", "exact", "float"}:
        raise ValueError("comparison must be tokens, exact, or float")
    for name in ("atol", "rtol"):
        value = getattr(config, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{name} must be a nonnegative finite number")
    if config.url is not None and not isinstance(config.url, str):
        raise ValueError("problem.url must be a string")
