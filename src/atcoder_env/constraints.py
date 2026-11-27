"""Import supported statement constraints into a problem workspace."""

from __future__ import annotations

import hashlib
import importlib.metadata
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import requests
from bs4 import BeautifulSoup
from loguru import logger

from atcoder_env import __version__
from atcoder_env.artifacts import write_json
from atcoder_env.config import Config
from atcoder_env.constraint_parser import ConstraintAnalysis, analyze_statement
from atcoder_env.external import SOURCE_URL, task_parts
from atcoder_env.graph_constraints import GraphConstraints
from atcoder_env.official_corpus import (
    download_archive,
    install_corpus,
    is_heuristic,
    official_archive,
)

MAX_HTML_BYTES = 4 * 1024 * 1024
GENERATED_CASES = f'''"""Input strategies generated from the adjacent constraints.toml file."""

# Reusable environment source: {SOURCE_URL}
from pathlib import Path

from atcoder_env.constraint_io import load_constraints

_constraints = load_constraints(Path(__file__).with_name("constraints.toml"))

strategy = _constraints.strategy
serialize = _constraints.serialize
edge_cases = _constraints.edge_cases
benchmark_case = _constraints.benchmark_case
benchmark_patterns = _constraints.benchmark_patterns
benchmark_strategy = _constraints.benchmark_strategy
'''


def fetch_statement(url: str) -> bytes:
    """Fetch a public English task page with network timeouts and a size limit."""
    contest, task = task_parts(url)
    english_url = f"https://atcoder.jp/contests/{contest}/tasks/{task}?lang=en"
    try:
        with requests.get(
            english_url,
            timeout=(15, 30),
            stream=True,
            allow_redirects=False,
            headers={"User-Agent": f"atcoder-environment/{__version__} (+{SOURCE_URL})"},
        ) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(
                    f"Expected a public task page, received HTTP {response.status_code}. "
                    "Download the English page yourself and use --html FILE if needed."
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    raise ValueError("Task page exceeds the 4 MiB parsing limit")
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException as error:
        raise ValueError(f"Could not download the public English task page: {error}") from error


def _manual_edits(root: Path, analysis: ConstraintAnalysis) -> dict:
    """Show the exact statement data and the generator hooks to implement."""
    input_format = (
        BeautifulSoup(analysis.input_format, "html.parser").get_text().strip()
        if analysis.input_format
        else None
    )
    functions = {
        "strategy()": (
            "Return a Hypothesis strategy for the complete input; apply every official "
            "constraint shown in this report, including relations between fields. Limit sizes "
            "for the brute-force reference solution."
        ),
        "serialize(case)": (
            "Return the exact input format shown in this report as text, including derived counts, "
            "row order, spaces and newlines."
        ),
        "edge_cases()": (
            "Return valid small inputs covering lower/upper bounds, equality and "
            "special structures allowed by these constraints."
        ),
        "benchmark_case(size, seed)": (
            "Generate a reproducible valid large input. Decide which input dimension "
            "size controls; keep all other fields and dependent counts consistent."
        ),
        "benchmark_patterns(size)": (
            "Optional: enumerate feasible input distributions; make benchmark_case "
            "accept pattern=... so ac bench can exercise them automatically."
        ),
        "benchmark_strategy(max_size, seed, pattern=...)": (
            "Optional: return a Hypothesis strategy that can shrink resource failures "
            "without violating the input constraints."
        ),
    }
    return {
        "file": str(root / "cases.py"),
        "input_format": input_format,
        "constraints": analysis.constraints,
        "functions": functions,
    }


def _log_manual_edits(edits: dict, report_path: Path) -> None:
    logger.warning(
        "Constraints were not installed; edit {} (replace the example generator or generated bindings).",
        edits["file"],
    )
    logger.warning("Input format for serialize(case):")
    if edits["input_format"]:
        for line in edits["input_format"].splitlines():
            logger.warning("  {}", line.strip())
    else:
        logger.warning("  Input format was not extracted; use the problem's Input section.")
    logger.warning("Official constraints for strategy(), edge_cases(), and benchmark_case():")
    if edits["constraints"]:
        for clause in edits["constraints"]:
            logger.warning("  - {}", clause)
    else:
        logger.warning("  No constraints were extracted; use the problem's Constraints section.")
    for function, instruction in edits["functions"].items():
        logger.warning("  {}: {}", function, instruction)
    logger.warning("Do not use an unchanged example generator. Full analysis: {}", report_path)


def import_constraints(
    config: Config,
    *,
    url: str | None = None,
    max_size: int = 30,
    html_path: Path | None = None,
    tools_zip: Path | None = None,
) -> int:
    """Write a complete supported generator, or report unresolved constraints."""
    url = url or config.url
    if not url:
        raise ValueError("Set [problem].url in atcoder.toml or provide --url")
    contest, task = task_parts(url)
    url = f"https://atcoder.jp/contests/{contest}/tasks/{task}"
    folder = config.root / ".ac" / "constraints" / uuid4().hex
    folder.mkdir(parents=True)
    report: dict = {
        "url": url,
        "status": "ERROR",
        "stress_max_size": max_size,
        "parser_versions": {
            name: importlib.metadata.version(name)
            for name in (
                "atcoder-environment",
                "online-judge-template-generator",
                "sympy",
                "antlr4-python3-runtime",
            )
        },
    }
    logger.info("Constraint analysis: {}", folder)
    try:
        if html_path is not None:
            if html_path.stat().st_size > MAX_HTML_BYTES:
                raise ValueError("Task page exceeds the 4 MiB parsing limit")
            html = html_path.read_bytes()
        else:
            html = fetch_statement(url)
        (folder / "statement.html").write_bytes(html)
        report["statement_sha256"] = hashlib.sha256(html).hexdigest()
        if is_heuristic(url):
            # The final-system-test task pages intentionally have no statement.
            # Only alias a genuine empty A-Final page in the same contest.
            page = BeautifulSoup(html, "html.parser")
            source_task_url = url
            if page.select_one(".lang-en") is None and task == contest + "_b":
                title = page.title.get_text(" ", strip=True) if page.title else ""
                if "A-Final" not in title or "System Test" not in title:
                    raise ValueError("No English AHC statement; cannot identify a final-test alias")
                if html_path is not None:
                    raise ValueError(
                        "For offline final-test import use the original A statement HTML"
                    )
                source_task_url = f"https://atcoder.jp/contests/{contest}/tasks/{contest}_a"
                html = fetch_statement(source_task_url)
                (folder / "source-statement.html").write_bytes(html)
            source, interactive = official_archive(html, source_task_url)
            if tools_zip is not None:
                if tools_zip.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError("Official tools ZIP exceeds local download cap")
                data = tools_zip.read_bytes()
            elif html_path is not None:
                raise ValueError("Offline AHC import requires --tools-zip FILE")
            else:
                data = download_archive(source)
            model = install_corpus(config.root, url, source, data, interactive)
            (config.root / "constraints.toml").write_text(model.to_toml(), encoding="utf-8")
            bindings = GENERATED_CASES.replace(
                "benchmark_strategy = _constraints.benchmark_strategy",
                "official_corpus = True\ninteractive_corpus = _constraints.interactive",
            )
            (config.root / "cases.py").write_text(bindings, encoding="utf-8")
            report.update(
                status="INSTALLED",
                method="official_corpus",
                source_task_url=source_task_url,
                source_url=source,
                archive_sha256=model.archive_sha256,
                input_count=model.count,
                interactive=model.interactive,
                offline_archive_supplied=tools_zip is not None,
            )
            return 0
        if tools_zip is not None:
            raise ValueError("--tools-zip is only for AHC official input archives")
        analysis = analyze_statement(html, url, max_size=max_size)
        report.update(
            input_format=analysis.input_format,
            constraints=analysis.constraints,
            recognized=analysis.recognized,
            unsupported=analysis.unsupported,
            method=analysis.method,
        )
        if analysis.spec is None:
            report["status"] = "UNSUPPORTED"
            for condition in analysis.unsupported:
                logger.warning("Unresolved: {}", condition)
            report["manual_edits"] = _manual_edits(config.root, analysis)
            _log_manual_edits(report["manual_edits"], folder / "report.json")
            return 2
        spec = analysis.spec
        report["spec"] = asdict(spec)
        if analysis.method == "reviewed_schema":
            logger.info(
                "Using a reviewed structured schema matched to the official statement fingerprint"
            )
        (config.root / "constraints.toml").write_text(spec.to_toml(), encoding="utf-8")
        (config.root / "cases.py").write_text(GENERATED_CASES, encoding="utf-8")
        report["status"] = "INSTALLED"
        logger.info("Installed constraints.toml and cases.py in {}", config.root)
        if isinstance(spec, GraphConstraints):
            logger.info(
                "Official {}: {}..{}, {}: {}..{}; endpoints 1..{}, {} != {}, labels 1..{}. "
                "Stress cap for both counts: {}; bench size controls {}.",
                spec.vertex_name,
                spec.vertex_min,
                spec.vertex_max,
                spec.edge_name,
                spec.edge_min,
                spec.edge_max,
                spec.vertex_name,
                spec.source_name,
                spec.target_name,
                spec.edge_name,
                spec.stress_max_size,
                spec.edge_name,
            )
        else:
            logger.info(
                "Official {}: {}..{}; stress maximum: {}. Inspect constraints.toml before testing.",
                spec.size_name,
                spec.size_min,
                spec.size_max,
                min(spec.stress_max_size, spec.size_max),
            )
        logger.info("Implement main.py and brute.py, then run ac stress {}", config.root)
        return 0
    except (OSError, ValueError) as error:
        report["error"] = str(error)
        logger.error("Constraint import failed: {}", error)
        return 2
    finally:
        write_json(folder / "report.json", report)
