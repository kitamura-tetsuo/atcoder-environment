"""Validate the labeled-edge model and its integration with the existing parser."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import find, given, settings

from atcoder_env import cli
from atcoder_env.benchmark_sizes import choose_benchmark_sizes
from atcoder_env.constraint_io import load_constraints
from atcoder_env.constraint_parser import analyze_statement
from atcoder_env.graph_constraints import GRAPH_PATTERNS, GraphConstraints
from atcoder_env.harness import case_module
from atcoder_env.workspace import create_workspace

URL = "https://atcoder.jp/contests/abc485/tasks/abc485_e"
FIXTURE = Path(__file__).with_name("fixtures") / "abc485_e_constraints.html"
SPEC = GraphConstraints("N", 2, 200_000, "M", 1, 200_000, "U", "V", "C", url=URL)


def assert_case(case, *, vertex_max=200_000, edge_max=200_000):
    n, edges = case
    m = len(edges)
    assert 2 <= n <= vertex_max
    assert 1 <= m <= edge_max
    assert all(1 <= u <= n and 1 <= v <= n and u != v and 1 <= c <= m for u, v, c in edges)
    return n, edges


def test_official_graph_constraints():
    result = analyze_statement(FIXTURE.read_bytes(), URL)
    assert result.unsupported == []
    assert result.recognized == result.constraints
    assert len(result.recognized) == 6
    assert result.spec == SPEC


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (r"U_i, V_i", r"U_i, V_j"),
        (r"U_i, V_i", r"U_M, V_M"),
        (r"U_i, V_i", r"U_i, W_i"),
        (r"U_i \neq V_i", r"U_i \neq V_j"),
        (r"U_i \neq V_i", r"U_i \neq C_i"),
        (r"U_i \neq V_i", r"U_M \neq V_M"),
        (r"1 \leq C_i \leq M", r"1 \leq C_i \leq N"),
        (r"1 \leq U_i, V_i \leq N", r"2 \leq U_i, V_i \leq N"),
        (r"All input values are integers.", r"All input values are real numbers."),
        (r"</ul>", r"<li>There are no parallel edges.</li></ul>"),
        (r"</ul>", r"<li>The graph is connected.</li></ul>"),
        (r"V_M", r"V_N"),
    ],
)
def test_graph_parser_rejects_unmodeled_constraints(old, new):
    source = FIXTURE.read_text()
    assert old in source
    result = analyze_statement(source.replace(old, new).encode(), URL)
    assert result.spec is None
    assert result.unsupported


def test_graph_parser_supports_separate_endpoint_bounds_and_complete_domain():
    text = FIXTURE.read_text().replace(
        r"1 \leq U_i, V_i \leq N",
        r"1 \leq U_i \leq N (1 \leq i \leq M)</var></li><li><var>1 \leq V_i \leq N",
    )
    result = analyze_statement(text.encode(), URL)
    assert result.spec == SPEC, result.unsupported


@given(SPEC.strategy())
@settings(max_examples=75, deadline=None)
def test_small_cases_are_valid(case):
    n, edges = assert_case(case, vertex_max=30, edge_max=30)
    lines = SPEC.serialize(case).splitlines()
    assert lines[0] == f"{n} {len(edges)}"
    assert [tuple(map(int, line.split())) for line in lines[1:]] == edges


@pytest.mark.parametrize("pattern", GRAPH_PATTERNS)
@pytest.mark.parametrize("size", [1, 2, 37, 2000])
def test_benchmark_patterns_valid_deterministic(pattern, size):
    case = SPEC.benchmark_case(size, 123, pattern=pattern)
    assert_case(case)
    assert len(case[1]) == size
    assert case == SPEC.benchmark_case(size, 123, pattern=pattern)


def test_patterns_cover_independent_counts_colors_and_parallel_edges():
    n, edges = SPEC.benchmark_case(1, 0, pattern="sparse")
    assert n == 200_000 and len(edges) == 1
    n, edges = SPEC.benchmark_case(100, 0, pattern="parallel")
    assert n == 2
    assert len({(u, v) for u, v, _ in edges}) == 1
    assert len({c for _, _, c in edges}) == 100
    _, edges = SPEC.benchmark_case(100, 0, pattern="hub_distinct")
    assert all(2 in (u, v) for u, v, _ in edges)
    assert len({c for _, _, c in edges}) == 100
    _, edges = SPEC.benchmark_case(100, 0, pattern="hub_same")
    assert {c for _, _, c in edges} == {1}


def test_full_size_and_explicit_oversize():
    for pattern in ("default", "hub_distinct", "random"):
        case = SPEC.benchmark_case(200_000, 1, pattern=pattern)
        assert_case(case)
        assert len(case[1]) == 200_000
    spec = replace(SPEC, vertex_max=5, edge_max=8)
    with pytest.raises(ValueError, match="official range"):
        spec.benchmark_case(9, 0)
    case = spec.benchmark_case(9, 0, allow_out_of_range=True)
    assert_case(case, vertex_max=5, edge_max=9)
    assert len(spec.serialize(case, allow_out_of_range=True).splitlines()) == 10
    with pytest.raises(ValueError, match="official range"):
        spec.serialize(case)


@pytest.mark.parametrize(
    "case",
    [
        (1, [(1, 2, 1)]),
        (2, []),
        (2, [(1, 1, 1)]),
        (2, [(1, 3, 1)]),
        (2, [(1, 2, 0)]),
        (2, [(1, 2, 2)]),
        (2, [(True, 2, 1)]),
    ],
)
def test_invalid_graph_cases_fail(case):
    with pytest.raises(ValueError):
        SPEC.serialize(case)


@pytest.mark.parametrize("pattern", GRAPH_PATTERNS)
@pytest.mark.parametrize("size_only", [True, False])
def test_compact_benchmark_strategy_can_shrink_valid_graph(pattern, size_only):
    strategy = SPEC.benchmark_strategy(50, 3, pattern=pattern, size_only=size_only)
    case = find(
        strategy,
        lambda case: len(case[1]) >= 5,
        settings=settings(max_examples=200, deadline=None, database=None),
    )
    assert_case(case)
    assert len(case[1]) == 5
    if not size_only:
        assert case[0] == 2


def test_edge_cases_respect_both_stress_caps():
    for case in SPEC.edge_cases():
        assert_case(case, vertex_max=30, edge_max=30)


def test_cli_import_graph_and_benchmark(tmp_path):
    root = create_workspace(tmp_path / "problem", url=URL)
    main = root / "main.py"
    main.write_text("import sys\nprint(len(sys.stdin.readlines()) - 1)\n")
    brute = root / "brute.py"
    brute.write_text(main.read_text())
    assert cli.main(["constraints", str(root), "--html", str(FIXTURE)]) == 0
    assert load_constraints(root / "constraints.toml") == SPEC
    assert choose_benchmark_sizes(root) == [1, 10, 100, 1000, 10000, 100000, 200000]
    assert choose_benchmark_sizes(root, [200001]) == [200001]
    with case_module(root) as module:
        assert set(module.benchmark_patterns(5)) == set(GRAPH_PATTERNS)
        assert module.serialize((2, [(1, 2, 1), (1, 2, 1)])) == "2 2\n1 2 1\n1 2 1\n"
    assert cli.main(["stress", str(root), "--max-examples", "10"]) == 0
    assert (
        cli.main(["bench", str(root), "--sizes", "4", "--repeat", "1", "--pattern", "hub_distinct"])
        == 0
    )
    session = next((root / ".ac/runs").glob("*-bench-*"))
    data = json.loads((session / "summary.json").read_text())
    assert data["rows"][0]["pattern"] == "hub_distinct"
    assert data["rows"][0]["status"] == "ok"
    assert (session / "size-4-hub_distinct-run-1/input.txt").read_text().startswith("5 4\n")


def test_graph_schema_rejects_unmodeled_settings(tmp_path):
    path = tmp_path / "constraints.toml"
    for old, new in (
        ("allow_parallel = true", "allow_parallel = false"),
        ("allow_self_loops = false", "allow_self_loops = true"),
        ('label_max = "M"', 'label_max = "N"'),
        ("endpoint_min = 1", "endpoint_min = true"),
        ("[vertices]", "[vertices]\nunknown = 1"),
    ):
        path.write_text(SPEC.to_toml().replace(old, new))
        with pytest.raises(ValueError):
            load_constraints(path)
