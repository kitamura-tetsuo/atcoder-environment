"""Exercise generated workspaces and the complete offline demonstration."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from hypothesis import find, given, settings

from atcoder_env.workspace import create_workspace

REPOSITORY = Path(__file__).resolve().parents[1]


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"fixture_{path.parent.name}_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_tree(case):
    n, edges = case
    assert n >= 1
    assert len(edges) == n - 1
    adjacency = {vertex: [] for vertex in range(1, n + 1)}
    for left, right in edges:
        assert left in adjacency and right in adjacency
        assert left != right
        adjacency[left].append(right)
        adjacency[right].append(left)
    visited = set()
    pending = [1]
    while pending:
        vertex = pending.pop()
        if vertex not in visited:
            visited.add(vertex)
            pending.extend(adjacency[vertex])
    assert len(visited) == n


def test_create_workspace_and_escape_problem_url(tmp_path):
    url = 'https://atcoder.jp/contests/abc001/tasks/abc001_1?quoted="x"&slash=\\'
    destination = create_workspace(tmp_path / "problem", url=url)
    assert destination == tmp_path / "problem"
    assert (destination / "test").is_dir()
    config = tomllib.loads((destination / "atcoder.toml").read_text())
    assert config["problem"]["url"] == url
    assert config["run"]["solution"] == ["python", "main.py"]
    for name in ("main.py", "brute.py"):
        text = (destination / name).read_text()
        assert "https://github.com/kitamura-tetsuo/atcoder-environment" in text
        result = subprocess.run(
            [sys.executable, str(destination / name)],
            input="1\n0\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert result.returncode != 0
        assert "NotImplementedError" in result.stderr


def test_refuse_existing_contents_without_overwrite(tmp_path):
    destination = tmp_path / "problem"
    destination.mkdir()
    existing = destination / "main.py"
    existing.write_text("print('user solution')\n")
    with pytest.raises(FileExistsError, match="new or empty"):
        create_workspace(destination)
    assert existing.read_text() == "print('user solution')\n"
    assert list(destination.iterdir()) == [existing]


def test_existing_empty_directory_is_supported(tmp_path):
    create_workspace(tmp_path, template="tree")
    assert (tmp_path / "cases.py").exists()


def test_refuse_existing_file_and_symlink(tmp_path):
    existing = tmp_path / "file"
    existing.write_text("keep")
    with pytest.raises(FileExistsError):
        create_workspace(existing)
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(FileExistsError, match="symbolic-link"):
        create_workspace(alias)
    assert not list(target.iterdir())


def test_unknown_template_does_not_create_directory(tmp_path):
    destination = tmp_path / "problem"
    with pytest.raises(ValueError, match="Unknown template"):
        create_workspace(destination, template="missing")
    assert not destination.exists()


def test_array_template_round_trips_and_benchmarks(tmp_path):
    path = create_workspace(tmp_path / "array")
    cases = load_module(path / "cases.py")

    @settings(max_examples=30, deadline=None)
    @given(cases.strategy())
    def check(case):
        data = list(map(int, cases.serialize(case).split()))
        assert data[0] == len(data[1:])
        assert data[1:] == case
        assert 1 <= len(case) <= 30
        assert all(-100 <= value <= 100 for value in case)

    check()
    assert cases.benchmark_case(100, 7) == cases.benchmark_case(100, 7)
    assert len(cases.benchmark_case(100, 7)) == 100
    with pytest.raises(ValueError):
        cases.benchmark_case(0, 0)


def test_tree_template_always_generates_valid_trees_and_shrinks(tmp_path):
    path = create_workspace(tmp_path / "tree", template="tree")
    cases = load_module(path / "cases.py")

    @settings(max_examples=40, deadline=None)
    @given(cases.strategy())
    def check(case):
        validate_tree(case)
        data = list(map(int, cases.serialize(case).split()))
        assert len(data) == 1 + 2 * (data[0] - 1)

    check()
    shrunk = find(
        cases.strategy(),
        lambda case: case[0] >= 3,
        settings=settings(max_examples=100, database=None, deadline=None),
    )
    validate_tree(shrunk)
    assert shrunk[0] == 3
    for case in cases.edge_cases():
        validate_tree(case)
    large = cases.benchmark_case(100, 12)
    validate_tree(large)
    assert large == cases.benchmark_case(100, 12)


def test_offline_example_samples_and_differential_oracle():
    example = REPOSITORY / "examples" / "pair-sum"
    main = load_module(example / "main.py")
    brute = load_module(example / "brute.py")
    buggy = load_module(example / "buggy.py")
    cases = load_module(example / "cases.py")
    for sample in (example / "test").glob("*.in"):
        expected = sample.with_suffix(".out").read_text()
        assert main.solve(sample.read_text()) == expected
        assert brute.solve(sample.read_text()) == expected

    @settings(max_examples=100, deadline=None)
    @given(cases.strategy())
    def check(case):
        data = cases.serialize(case)
        assert main.solve(data) == brute.solve(data)

    check()
    duplicate_case = cases.serialize((0, [0, 0, 0]))
    assert main.solve(duplicate_case) == "3\n"
    assert buggy.solve(duplicate_case) == "2\n"


def test_optional_helpers_are_seeded_and_unweighted():
    pytest.importorskip("onlinejudge_random")
    pytest.importorskip("cyaron")
    helpers = load_module(REPOSITORY / "examples" / "generators" / "optional_helpers.py")
    assert helpers.boundary_integer(42) == helpers.boundary_integer(42)
    assert 1 <= helpers.boundary_integer(42) <= 200_000
    text = helpers.cyaron_tree_text(10, 42)
    assert text == helpers.cyaron_tree_text(10, 42)
    lines = text.splitlines()
    assert lines[0] == "10"
    edges = [tuple(map(int, line.split())) for line in lines[1:]]
    assert all(len(edge) == 2 for edge in edges)
    validate_tree((10, edges))
