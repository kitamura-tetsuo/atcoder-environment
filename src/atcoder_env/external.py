"""Explicit adapters for network-dependent online-judge-tools commands."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from loguru import logger

from atcoder_env.config import Config
from atcoder_env.runner import run_program

SOURCE_URL = "https://github.com/kitamura-tetsuo/atcoder-environment"


def task_parts(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "atcoder.jp"
        or len(parts) != 4
        or parts[0] != "contests"
        or parts[2] != "tasks"
        or any(
            not part
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in part
            )
            for part in (parts[1], parts[3])
        )
    ):
        raise ValueError("Use an https://atcoder.jp/contests/<contest>/tasks/<problem> URL")
    return parts[1], parts[3]


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ValueError(f"{name} not found. Run uv sync --frozen and use uv run ac ...")
    return path


def checked_external(command: list[str], *, cwd: Path, log_path: Path) -> str:
    result = run_program(command, "", cwd=cwd, timeout=90, output_limit=8 * 1024 * 1024)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.stderr:
        logger.info("{}", result.stderr.rstrip())
    if result.status != "ok":
        raise ValueError(
            f"External tool failed ({result.status}); see {log_path}. "
            "Local testing still works; authentication or website changes may require a browser."
        )
    return result.stdout


def download_samples(config: Config, url: str | None = None) -> int:
    url = url or config.url
    if not url:
        raise ValueError("Set [problem].url in atcoder.toml or provide --url")
    task_parts(url)
    staging = config.root / ".ac" / "downloads" / uuid4().hex
    staging.mkdir(parents=True)
    downloaded = staging / "test"
    output = checked_external(
        [executable("oj"), "download", url, "--directory", str(downloaded)],
        cwd=config.root,
        log_path=staging / "download.log",
    )
    if output:
        logger.info("{}", output.rstrip())
    inputs = sorted(downloaded.glob("*.in"))
    if not inputs or any(not path.with_suffix(".out").is_file() for path in inputs):
        raise ValueError(f"No complete samples downloaded; inspect {staging}")
    files = [file for path in inputs for file in (path, path.with_suffix(".out"))]
    destination = config.root / "test"
    destination.mkdir(exist_ok=True)
    for file in files:
        target = destination / file.name
        if target.exists() and target.read_bytes() != file.read_bytes():
            raise ValueError(
                f"Refusing to overwrite existing test: {target}; downloaded files are in {downloaded}"
            )
    for file in files:
        target = destination / file.name
        if not target.exists():
            with target.open("xb") as handle:
                handle.write(file.read_bytes())
    logger.info("Downloaded {} sample tests to {}", len(inputs), destination)
    return 0


def generate_templates(config: Config) -> int:
    if not config.url:
        raise ValueError("Set [problem].url in atcoder.toml first")
    task_parts(config.url)
    folder = config.root / ".ac" / "templates" / uuid4().hex
    folder.mkdir(parents=True)
    for template in ("main.py", "generate.py"):
        source = checked_external(
            [executable("oj-template"), "-t", template, config.url],
            cwd=config.root,
            log_path=folder / f"{template}.log",
        )
        header = f"# Reusable environment source: {SOURCE_URL}\n"
        (folder / template).write_text(header + source, encoding="utf-8")
    logger.info("Generated drafts: {}", folder)
    logger.warning(
        "Inspect syntax, input format, and TODO bounds before using generated code. "
        "These files are never executed automatically."
    )
    return 0


def submit(config: Config, *, file: str, language: str | None) -> int:
    if not config.url:
        raise ValueError("Set [problem].url in atcoder.toml first")
    task_parts(config.url)
    source = (config.root / file).resolve()
    if not source.is_file():
        raise ValueError(f"Source file not found: {source}")
    args = [executable("oj"), "submit", config.url, str(source)]
    if language:
        args.extend(["--language", language])
    logger.info("Submitting {} to {} via oj's interactive confirmation", source, config.url)
    # Preserve oj's confirmation and terminal input. Never capture passwords in a log.
    return subprocess.call(args, cwd=config.root)
