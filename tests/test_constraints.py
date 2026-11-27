"""Verify automatic import, regeneration, and execution provenance end to end."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from atcoder_env import cli, constraints
from atcoder_env.artifacts import new_session
from atcoder_env.config import load_config
from atcoder_env.harness import case_module
from atcoder_env.workspace import create_workspace

URL = "https://atcoder.jp/contests/abc485/tasks/abc485_c"
FIXTURE = Path(__file__).with_name("fixtures") / "abc485_c_constraints.html"


def report_for(root: Path) -> tuple[Path, dict]:
    folder = next((root / ".ac" / "constraints").iterdir())
    return folder, json.loads((folder / "report.json").read_text())


def test_offline_cli_import_uses_live_format_and_snapshots_configuration(tmp_path: Path) -> None:
    root = create_workspace(tmp_path / "problem", url=URL)
    main_before = (root / "main.py").read_bytes()
    brute_before = (root / "brute.py").read_bytes()
    assert cli.main(["constraints", str(root), "--html", str(FIXTURE)]) == 0
    folder, report = report_for(root)
    assert report["status"] == "INSTALLED"
    assert report["unsupported"] == []
    assert len(report["recognized"]) == 4
    assert report["spec"]["size_max"] == 300000
    assert report["spec"]["values_max"] == "N"
    assert report["spec"]["min_distinct"] == 2
    assert report["statement_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert (folder / "statement.html").read_bytes() == FIXTURE.read_bytes()
    assert (root / "main.py").read_bytes() == main_before
    assert (root / "brute.py").read_bytes() == brute_before
    with case_module(root) as module:
        for colors in module.edge_cases():
            assert 2 <= len(colors) <= 30
            assert all(1 <= color <= len(colors) for color in colors)
            assert len(set(colors)) >= 2
            tokens = module.serialize(colors).split()
            assert int(tokens[0]) == len(colors)
            assert list(map(int, tokens[1:])) == colors
    session = new_session(load_config(root), "test")
    assert (session / "sources" / "constraints.toml").read_bytes() == (
        root / "constraints.toml"
    ).read_bytes()
    hashes = json.loads((session / "environment.json").read_text())["source_sha256"]
    assert (
        hashes["constraints.toml"]
        == hashlib.sha256((root / "constraints.toml").read_bytes()).hexdigest()
    )
    log = (root / ".ac" / "logs" / "ac.log").read_text()
    assert "Installed constraints.toml" in log


def test_unknown_clause_never_installs_partial_generator(tmp_path: Path) -> None:
    root = create_workspace(tmp_path / "problem", url=URL)
    before = (root / "cases.py").read_bytes()
    html = FIXTURE.read_text().replace("</ul>", "<li>The sum of the colors is even.</li></ul>")
    source = tmp_path / "unknown.html"
    source.write_text(html)
    assert cli.main(["constraints", str(root), "--html", str(source)]) == 2
    folder, report = report_for(root)
    assert report["status"] == "UNSUPPORTED"
    assert report["unsupported"]
    assert not (root / "constraints.toml").exists()
    assert not (folder / "cases.py").exists()
    assert (root / "cases.py").read_bytes() == before
    edits = report["manual_edits"]
    assert edits["file"] == str(root / "cases.py")
    assert "<var>" not in edits["input_format"]
    assert "C_1" in edits["input_format"]
    assert "The sum of the colors is even." in edits["constraints"]
    assert set(edits["functions"]) == {
        "strategy()",
        "serialize(case)",
        "edge_cases()",
        "benchmark_case(size, seed)",
        "benchmark_patterns(size)",
        "benchmark_strategy(max_size, seed, pattern=...)",
    }
    log = (root / ".ac" / "logs" / "ac.log").read_text()
    for function in edits["functions"]:
        assert function in log
    assert "The sum of the colors is even." in log


def test_successful_import_replaces_edited_generator_and_configuration(tmp_path: Path) -> None:
    root = create_workspace(tmp_path / "problem", url=URL)
    (root / "cases.py").write_text("raise RuntimeError('old generator')\n")
    (root / "constraints.toml").write_text("old configuration\n")
    assert cli.main(["constraints", str(root), "--html", str(FIXTURE), "--max-size", "12"]) == 0
    _, report = report_for(root)
    assert report["status"] == "INSTALLED"
    with case_module(root) as module:
        assert max(map(len, module.edge_cases())) == 12
        assert module.serialize([1, 2]) == "2\n1 2\n"


@pytest.mark.parametrize("supported", [True, False])
def test_prepare_imports_constraints_after_samples_and_reports_incomplete_work(
    tmp_path: Path, monkeypatch, supported: bool
) -> None:
    root = tmp_path / "problem"
    html = FIXTURE.read_bytes()
    if not supported:
        html = html.replace(b"</ul>", b"<li>The values have a hidden extra condition.</li></ul>")

    def fake_download(config):
        (config.root / "test" / "sample.in").write_text("2\n1 2\n")
        (config.root / "test" / "sample.out").write_text("1 1\n")
        return 0

    monkeypatch.setattr(cli, "download_samples", fake_download)
    monkeypatch.setattr(constraints, "fetch_statement", lambda url: html)
    result = cli.main(["prepare", URL, "--directory", str(root)])
    assert result == (0 if supported else 2)
    assert (root / "test" / "sample.in").is_file()
    assert (root / "constraints.toml").exists() == supported


def test_prepare_can_explicitly_skip_constraint_fetch(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "problem"
    monkeypatch.setattr(cli, "download_samples", lambda config: 0)

    def unexpected_fetch(url):
        pytest.fail("--no-constraints must avoid fetching the task page")

    monkeypatch.setattr(constraints, "fetch_statement", unexpected_fetch)
    assert cli.main(["prepare", URL, "-d", str(root), "--no-constraints"]) == 0
    assert not (root / "constraints.toml").exists()
    assert not (root / ".ac" / "constraints").exists()


def test_unsupported_input_shape_prints_actual_format_and_edit_targets(tmp_path):
    root = create_workspace(tmp_path / "problem", url=URL)
    fixture = Path(__file__).with_name("fixtures") / "abc485_e_constraints.html"
    text = fixture.read_text().replace("<var>C_M</var>", "<var>C_N</var>")
    source = tmp_path / "unsupported.html"
    source.write_text(text)
    assert cli.main(["constraints", str(root), "--html", str(source)]) == 2
    _, report = report_for(root)
    assert report["status"] == "UNSUPPORTED"
    assert report["manual_edits"]["input_format"].startswith("N M\nU_1 V_1 C_1")
    assert len(report["manual_edits"]["constraints"]) == 6
    log = (root / ".ac" / "logs" / "ac.log").read_text()
    for expected in (
        "Unsupported input format",
        "N M",
        "U_1 V_1 C_1",
        "strategy()",
        "serialize(case)",
        "benchmark_case(size, seed)",
    ):
        assert expected in log
    assert str(root / "cases.py") in log
