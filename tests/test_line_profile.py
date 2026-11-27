"""Exercise isolated profiling, including partial reports at resource limits."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from atcoder_env.runner import run_program


def run_profile(tmp_path: Path, source: str, *, timeout: float = 2.0, memory_mib: int = 128):
    script = tmp_path / "main.py"
    script.write_text(source, encoding="utf-8")
    report = tmp_path / "profile"
    report.mkdir()
    result = run_program(
        [
            sys.executable,
            "-m",
            "atcoder_env.line_profile",
            str(report),
            str(timeout),
            str(memory_mib),
            str(script),
            "argument",
        ],
        "",
        cwd=tmp_path,
        timeout=timeout + 2,
        memory_limit_mib=memory_mib * 2,
    )
    status = json.loads((report / "profile-status.json").read_text(encoding="utf-8"))
    with (report / "line-profile.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert script.read_text(encoding="utf-8") == source
    return result, status, rows


def test_growing_memory_preserves_partial_line_profile(tmp_path: Path) -> None:
    result, status, rows = run_profile(
        tmp_path,
        "import time\n"
        "print('started', flush=True)\n"
        "blocks = []\n"
        "for _ in range(100):\n"
        "    blocks.append(bytearray(4 * 1024 * 1024))\n"
        "    time.sleep(0.01)\n"
        "print('finished')\n",
        timeout=5,
    )
    assert result.returncode == 125
    assert result.stdout == "started\n"
    assert status["status"] == "memory_limit"
    assert status["partial"] is True
    assert status["memory_limit_mib"] == 128
    assert status["max_rss_kib"] > 128 * 1024
    assert status["memory_monitor_error"] is None
    allocation = next(row for row in rows if row["line"] == "5")
    assert 1 <= int(allocation["hits"]) < 100
    assert float(allocation["seconds"]) > 0
    assert allocation["source"].strip() == "blocks.append(bytearray(4 * 1024 * 1024))"


def test_successful_profile_retains_arguments_and_reports_memory(tmp_path: Path) -> None:
    result, status, rows = run_profile(tmp_path, "import sys\nprint(sys.argv[1])\n")
    assert result.status == "ok"
    assert result.stdout == "argument\n"
    assert status["status"] == "ok"
    assert status["partial"] is False
    assert status["max_rss_kib"] > 0
    assert any(row["line"] == "2" for row in rows)


def test_deadline_still_flushes_with_memory_monitor_enabled(tmp_path: Path) -> None:
    result, status, rows = run_profile(tmp_path, "while True:\n    pass\n", timeout=0.1)
    assert result.returncode == 124
    assert status["status"] == "timeout"
    assert status["partial"] is True
    assert rows
    assert any(int(row["hits"]) > 1 for row in rows)


def test_profile_memory_ignores_large_launcher_allocation(tmp_path: Path) -> None:
    parent_allocation = bytearray(160 * 1024 * 1024)
    result, status, rows = run_profile(tmp_path, "print('small')\n", memory_mib=64)
    assert len(parent_allocation) == 160 * 1024 * 1024
    assert result.status == "ok"
    assert result.stdout == "small\n"
    assert status["status"] == "ok"
    assert 0 < status["max_rss_kib"] < 64 * 1024
    assert rows
