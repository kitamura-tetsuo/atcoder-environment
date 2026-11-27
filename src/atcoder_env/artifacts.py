"""Keep inspectable execution evidence outside submitted source files."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from loguru import logger

from atcoder_env.config import Config
from atcoder_env.runner import RunResult


def write_json(path: Path, data: dict | list) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def configure_logging(root: Path | None = None) -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
        level="INFO",
    )
    directory = (root or Path.cwd()) / ".ac" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    logger.add(
        directory / "ac.log", rotation="1 day", retention="14 days", encoding="utf-8", level="DEBUG"
    )


def new_session(config: Config, kind: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    session = config.root / ".ac" / "runs" / f"{stamp}-{kind}-{uuid4().hex[:6]}"
    session.mkdir(parents=True)
    source_dir = session / "sources"
    source_dir.mkdir()
    files = {"main.py", "brute.py", "cases.py", "atcoder.toml", "constraints.toml"}
    files.update(arg for arg in config.solution + config.oracle if arg.endswith(".py"))
    hashes = {}
    for name in sorted(files):
        source = (config.root / name).resolve()
        if source.is_file() and source.is_relative_to(config.root):
            content = source.read_bytes()
            relative = source.relative_to(config.root)
            target = source_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            hashes[str(relative)] = hashlib.sha256(content).hexdigest()
    write_json(
        session / "environment.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "python": sys.version,
            "package_versions": {
                name: importlib.metadata.version(name)
                for name in (
                    "atcoder-environment",
                    "hypothesis",
                    "cyaron",
                    "online-judge-tools",
                    "online-judge-template-generator",
                    "sympy",
                    "antlr4-python3-runtime",
                )
            },
            "workspace": str(config.root),
            "solution": config.solution,
            "oracle": config.oracle,
            "timeout": config.timeout,
            "oracle_timeout": config.oracle_timeout,
            "output_limit": config.output_limit,
            "comparison": config.comparison,
            "atol": config.atol,
            "rtol": config.rtol,
            "source_sha256": hashes,
        },
    )
    logger.info("Execution artifacts: {}", session)
    return session


def save_case(
    session: Path,
    name: str,
    *,
    input_data: str,
    actual: RunResult,
    expected: str | None = None,
    oracle: RunResult | None = None,
    verdict: str,
    details: dict | None = None,
) -> Path:
    target = session / name
    target.mkdir(parents=True, exist_ok=True)
    for filename, content in {
        "input.txt": input_data,
        "actual.txt": actual.stdout,
        "stderr.txt": actual.stderr,
    }.items():
        (target / filename).write_text(content, encoding="utf-8")
    if expected is not None:
        (target / "expected.txt").write_text(expected, encoding="utf-8")
    if oracle is not None:
        (target / "oracle.stdout.txt").write_text(oracle.stdout, encoding="utf-8")
        (target / "oracle.stderr.txt").write_text(oracle.stderr, encoding="utf-8")
    write_json(
        target / "result.json",
        {
            "verdict": verdict,
            "solution": actual.to_dict(),
            "oracle": oracle.to_dict() if oracle else None,
            **(details or {}),
        },
    )
    return target
