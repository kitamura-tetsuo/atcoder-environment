"""Exercise URL operands through real local workflows and offline network adapters."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from atcoder_env import cli, constraints
from atcoder_env.config import load_config

URL = "https://atcoder.jp/contests/abc485/tasks/abc485_c"
OTHER_URL = "https://atcoder.jp/contests/abc485/tasks/abc485_b"
VARIANT_URL = URL + "/?lang=en#sample-input"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "pair-sum"
HTML_FIXTURE = Path(__file__).with_name("fixtures") / "abc485_c_constraints.html"


def canonical_workspace(cwd: Path) -> Path:
    return cwd / "problems" / "abc485" / "abc485_c"


def copy_problem(destination: Path, *, url: str = URL) -> Path:
    shutil.copytree(EXAMPLE, destination, ignore=shutil.ignore_patterns(".ac", "__pycache__"))
    config = destination / "atcoder.toml"
    config.write_text(config.read_text().replace('url = ""', f'url = "{url}"', 1))
    return destination


def latest_session(root: Path, kind: str) -> Path:
    return sorted((root / ".ac" / "runs").glob(f"*-{kind}-*"))[-1]


@pytest.fixture(autouse=True)
def offline_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("This URL-target test must not contact an external service")

    for name in ("download_samples", "generate_templates", "submit"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(constraints, "fetch_statement", forbidden)


def test_url_runs_local_test_stress_input_and_benchmark_from_caller_directory(
    tmp_path: Path,
) -> None:
    root = copy_problem(canonical_workspace(tmp_path))
    assert cli.main(["test", VARIANT_URL]) == 0
    summary = json.loads((latest_session(root, "test") / "summary.json").read_text())
    assert summary["passed"] == 3

    assert cli.main(["stress", URL, "--max-examples", "5", "--seed", "7"]) == 0
    stress_summary = json.loads((latest_session(root, "stress") / "summary.json").read_text())
    assert stress_summary["status"] == "PASS"

    # Input and expected paths remain relative to the caller, not the solution directory.
    (tmp_path / "caller.in").write_text((root / "test" / "sample-1.in").read_text())
    (tmp_path / "caller.out").write_text((root / "test" / "sample-1.out").read_text())
    assert cli.main(["run", URL, "--input", "caller.in", "--expected", "caller.out"]) == 0
    assert (latest_session(root, "run") / "case" / "input.txt").read_text() == (
        tmp_path / "caller.in"
    ).read_text()

    assert cli.main(["bench", URL, "--sizes", "2,4", "--repeat", "1"]) == 0
    assert (latest_session(root, "bench") / "benchmark.csv").is_file()
    assert Path.cwd() == tmp_path
    assert not (tmp_path / ".ac" / "runs").exists()


def test_url_failure_can_be_replayed_and_promoted(tmp_path: Path) -> None:
    root = copy_problem(canonical_workspace(tmp_path))
    assert (
        cli.main(
            ["stress", URL, "--solution", "python buggy.py", "--no-shrink", "--max-examples", "1"]
        )
        == 1
    )
    failure = latest_session(root, "stress") / "failure"
    artifact = str(failure.relative_to(tmp_path))
    assert cli.main(["replay", URL, artifact]) == 0
    assert cli.main(["promote", URL, artifact, "--name", "url-regression"]) == 0
    assert (root / "test" / "url-regression.in").read_bytes() == (
        failure / "input.txt"
    ).read_bytes()
    assert (root / "test" / "url-regression.out").read_bytes() == (
        failure / "expected.txt"
    ).read_bytes()
    assert cli.main(["test", URL, "--solution", "python buggy.py"]) == 1


def test_explicit_directory_overrides_matching_current_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = copy_problem(tmp_path / "current")
    custom = copy_problem(tmp_path / "custom")
    monkeypatch.chdir(current)
    assert cli.main(["test", URL, "--directory", str(custom)]) == 0
    assert (custom / ".ac" / "runs").is_dir()
    assert not (current / ".ac" / "runs").exists()


def test_matching_current_workspace_is_reused_for_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_problem(tmp_path / "current", url=VARIANT_URL)
    monkeypatch.chdir(root)
    assert cli.main(["test", URL]) == 0
    assert (root / ".ac" / "runs").is_dir()
    assert not (root / "problems").exists()


def test_new_url_is_offline_and_stores_canonical_task(tmp_path: Path) -> None:
    assert cli.main(["new", VARIANT_URL]) == 0
    root = canonical_workspace(tmp_path)
    assert load_config(root).url == URL
    assert (root / "cases.py").is_file()
    assert not list((root / "test").glob("*.in"))
    assert not (root / "constraints.toml").exists()


@pytest.mark.parametrize("action", ["download", "template"])
def test_url_initializer_creates_workspace_and_only_dispatches_requested_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    seen = []

    def adapter(config, *args, **kwargs):
        seen.append((config.root, config.url))
        assert (config.root / "main.py").is_file()
        assert (config.root / "atcoder.toml").is_file()
        return 0

    monkeypatch.setattr(
        cli, {"download": "download_samples", "template": "generate_templates"}[action], adapter
    )
    assert cli.main([action, VARIANT_URL]) == 0
    root = canonical_workspace(tmp_path)
    assert seen == [(root, URL)]
    assert load_config(root).url == URL
    assert not (root / "constraints.toml").exists()


def test_constraints_url_creates_and_imports_from_saved_html_without_samples(
    tmp_path: Path,
) -> None:
    assert cli.main(["constraints", VARIANT_URL, "--html", str(HTML_FIXTURE)]) == 0
    root = canonical_workspace(tmp_path)
    assert load_config(root).url == URL
    assert (root / "constraints.toml").is_file()
    assert "load_constraints" in (root / "cases.py").read_text()
    assert not list((root / "test").glob("*.in"))


def test_prepare_uses_same_canonical_url_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    monkeypatch.setattr(
        cli, "download_samples", lambda config: seen.append((config.root, config.url)) or 0
    )
    assert cli.main(["prepare", VARIANT_URL, "--no-constraints"]) == 0
    assert seen == [(canonical_workspace(tmp_path), URL)]


def test_blank_stored_url_is_supplied_for_call_without_rewriting_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_problem(canonical_workspace(tmp_path), url="")
    config_path = root / "atcoder.toml"
    original = config_path.read_bytes()
    seen = []
    monkeypatch.setattr(
        cli, "generate_templates", lambda config: seen.append((config.root, config.url)) or 0
    )
    assert cli.main(["template", VARIANT_URL]) == 0
    assert seen == [(root, URL)]
    assert config_path.read_bytes() == original


def test_submit_url_routes_only_to_mock_adapter_and_propagates_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_problem(canonical_workspace(tmp_path))
    seen = []

    def submit_adapter(config, *, file, language):
        seen.append((config.root, config.url, file, language))
        return 17

    monkeypatch.setattr(cli, "submit", submit_adapter)
    assert cli.main(["submit", VARIANT_URL, "--file", "main.py", "--language", "5055"]) == 17
    assert seen == [(root, URL, "main.py", "5055")]


@pytest.mark.parametrize(
    "arguments",
    [
        ["test"],
        ["stress"],
        ["bench"],
        ["run", "--input", "missing.in"],
        ["replay", "missing-artifact"],
        ["promote", "missing-artifact", "--name", "regression"],
        ["submit"],
    ],
)
def test_execution_commands_do_not_implicitly_create_missing_url_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    assert cli.main([arguments[0], URL, *arguments[1:]]) == 2
    assert f"ac prepare {URL}" in capsys.readouterr().out
    assert not canonical_workspace(tmp_path).exists()


def test_empty_url_workspace_is_not_implicitly_initialized_by_test(tmp_path: Path) -> None:
    root = canonical_workspace(tmp_path)
    root.mkdir(parents=True)
    assert cli.main(["test", URL]) == 2
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("action", ["test", "submit"])
def test_stored_url_mismatch_blocks_execution_before_touching_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    root = copy_problem(canonical_workspace(tmp_path), url=OTHER_URL)
    before = (root / "atcoder.toml").read_bytes()

    def forbidden_dispatch(*args, **kwargs):
        pytest.fail("Mismatched task URL must be rejected before dispatch")

    monkeypatch.setattr(cli, "test_samples", forbidden_dispatch)
    assert cli.main([action, URL]) == 2
    assert (root / "atcoder.toml").read_bytes() == before
    assert not (root / ".ac").exists()


@pytest.mark.parametrize("action", ["download", "constraints"])
def test_conflicting_url_override_fails_before_workspace_creation(
    tmp_path: Path, action: str
) -> None:
    assert cli.main([action, URL, "--url", OTHER_URL]) == 2
    assert not canonical_workspace(tmp_path).exists()
    assert not (tmp_path / ".ac").exists()


def test_equivalent_url_override_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    monkeypatch.setattr(cli, "download_samples", lambda config, *args: seen.append(config.url) or 0)
    assert cli.main(["download", URL, "--url", VARIANT_URL]) == 0
    assert seen == [URL]


def test_directory_override_requires_url_operand(tmp_path: Path) -> None:
    local = copy_problem(tmp_path / "local")
    assert cli.main(["test", str(local), "--directory", str(tmp_path / "other")]) == 2
    assert not (local / ".ac").exists()


def test_invalid_non_atcoder_url_is_not_treated_as_local_path(tmp_path: Path) -> None:
    assert cli.main(["download", "https://example.com/contests/abc485/tasks/abc485_c"]) == 2
    assert not (tmp_path / "https:").exists()
    assert not (tmp_path / "problems").exists()
