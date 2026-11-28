from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from atcoder_env.cli import main
from atcoder_env.config import Config, load_config, stress_memory_limits
from atcoder_env.harness import execute
from atcoder_env.paired_runner import PairedRunner


def test_defaults_and_overrides(tmp_path):
    config = Config(root=tmp_path)
    assert config.oracle_timeout == 30
    assert stress_memory_limits(config) == (512, 2048)
    (tmp_path / "atcoder.toml").write_text(
        "[run]\nmemory_limit_mib = 64\noracle_timeout = 45\noracle_memory_limit_mib = 96\n"
    )
    config = load_config(tmp_path)
    assert config.oracle_timeout == 45
    assert stress_memory_limits(config) == (64, 96)


@pytest.mark.parametrize("parallel", [False, True])
def test_oracle_memory_limit_stops_real_process(tmp_path, parallel):
    config = Config(root=tmp_path, memory_limit_mib=32, oracle_memory_limit_mib=48)
    config.solution = [sys.executable, "-c", "print(1)"]
    config.oracle = [
        sys.executable,
        "-c",
        "import time; a=bytearray(80*1024*1024); time.sleep(2)",
    ]
    with PairedRunner(config, parallel=parallel) as runner:
        actual, oracle = runner.run("")
    assert actual is None
    assert oracle.status == "memory_limit"
    assert oracle.max_rss_kib >= 48 * 1024
    assert runner.stats["serial_retries"] == 0
    assert execute(config, "", oracle=True).status == "memory_limit"


def test_cli_oracle_memory_limit_saves_failure_and_effective_limits(tmp_path):
    example = Path(__file__).resolve().parents[1] / "examples" / "pair-sum"
    root = tmp_path / "problem"
    shutil.copytree(example, root, ignore=shutil.ignore_patterns(".ac", "__pycache__"))
    (root / "brute.py").write_text("import time\na = bytearray(80 * 1024 * 1024)\ntime.sleep(2)\n")
    assert (
        main(
            [
                "stress",
                str(root),
                "--memory-limit",
                "32",
                "--oracle-memory-limit",
                "48",
                "--oracle-timeout",
                "3",
                "--max-examples",
                "1",
            ]
        )
        == 2
    )
    run = next((root / ".ac" / "runs").glob("*-stress-*"))
    record = json.loads((run / "oracle-error" / "result.json").read_text())
    environment = json.loads((run / "environment.json").read_text())
    assert record["verdict"] == "ORACLE_MLE"
    assert record["oracle"]["status"] == "memory_limit"
    assert environment["memory_limit_mib"] == 32
    assert environment["oracle_memory_limit_mib"] == 48
    assert environment["oracle_timeout"] == 3
    assert (run / "oracle-error" / "input.txt").stat().st_size > 0
    assert json.loads((run / "summary.json").read_text())["status"] == "ERROR"
