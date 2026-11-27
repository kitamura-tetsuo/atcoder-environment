"""Regression coverage for the reviewed recent ABC schemas and general parser."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from hypothesis import given, settings
from hypothesis import strategies as st

from atcoder_env.constraint_io import load_constraints
from atcoder_env.constraint_parser import analyze_statement
from atcoder_env.reviewed_constraints import catalog, lookup, statement_digest
from atcoder_env.structured_constraints import StructuredConstraints, evaluate


def reviewed(task, cap=12):
    item = catalog()[task]
    return StructuredConstraints(
        item["size_name"],
        item["size_min"],
        item["size_max"],
        item["steps"],
        cap,
        f"https://atcoder.jp/contests/{task[:6]}/tasks/{task}",
        item["primary_step"],
    )


@pytest.mark.parametrize("task", sorted(catalog()))
def test_each_reviewed_schema_roundtrip_and_generators(task, tmp_path):
    model = reviewed(task)
    path = tmp_path / "constraints.toml"
    path.write_text(model.to_toml())
    assert load_constraints(path) == model
    sizes = model.default_benchmark_sizes()
    assert sizes[0] == model.size_min
    assert sizes[-1] == model.size_max
    assert all((n - model.size_min) % model.primary_step == 0 for n in sizes)
    for case in model.edge_cases():
        assert model.serialize(case).endswith("\n")
    for pattern in model.benchmark_patterns(model.effective_stress_max_size):
        # Bound secondary dimensions here; full-size online checks run separately.
        from atcoder_env.structured_constraints import Choices

        n = model.effective_stress_max_size
        case = model._make(Choices(42, pattern, size=n, cap=12), n)
        assert model.serialize(case).strip()
        assert case == model._make(Choices(42, pattern, size=n, cap=12), n)
    for case in model.edge_cases():
        assert model.serialize(case).strip()


def test_catalog_is_exactly_the_reviewed_abc_range():
    assert len(catalog()) == 63
    assert {name[:6] for name in catalog()} == {f"abc{i}" for i in range(476, 486)}


def statement(fmt, clauses):
    return (
        '<div id="task-statement"><span class="lang-en">'
        "<section><h3>Constraints</h3><ul>"
        + "".join(f"<li>{clause}</li>" for clause in clauses)
        + f"</ul></section><section><h3>Input</h3><pre>{fmt}</pre></section></span></div>"
    ).encode()


def test_generic_parser_multiple_arrays_and_fields():
    source = statement(
        "N M\nA_1 A_2 \\ldots A_N\nB_1 B_2 \\ldots B_M\n",
        [
            r"1 \le N,M \le 10",
            r"1 \le A_i \le M",
            r"-4 \le B_i \le 7",
            "All input values are integers.",
        ],
    )
    result = analyze_statement(source, "https://atcoder.jp/contests/abc999/tasks/abc999_c")
    assert result.method == "parsed_structured", result.unsupported
    assert result.spec is not None
    for case in result.spec.edge_cases():
        header, a, b = result.spec.serialize(case).splitlines()
        n, m = map(int, header.split())
        aa, bb = list(map(int, a.split())), list(map(int, b.split()))
        assert len(aa) == n and len(bb) == m
        assert all(1 <= x <= m for x in aa)
        assert all(-4 <= x <= 7 for x in bb)
    unknown = source.replace(b"</ul>", b"<li>Every element is unique.</li></ul>")
    assert (
        analyze_statement(unknown, "https://atcoder.jp/contests/abc999/tasks/abc999_c").spec is None
    )


def test_generic_parser_repeated_rows_and_dependent_bounds():
    source = statement(
        "N Q\nA_1 B_1\nA_2 B_2\n\\vdots\nA_Q B_Q\n",
        [
            r"2 \le N \le 10",
            r"1 \le Q \le 20",
            r"1 \le A_i,B_i \le N",
            "All input values are integers.",
        ],
    )
    model = analyze_statement(source, "https://atcoder.jp/contests/abc999/tasks/abc999_d").spec
    assert isinstance(model, StructuredConstraints)
    for case in model.edge_cases():
        first, *rows = model.serialize(case).splitlines()
        n, q = map(int, first.split())
        assert len(rows) == q
        assert all(
            len(row.split()) == 2 and all(1 <= int(x) <= n for x in row.split()) for row in rows
        )


def test_fingerprint_does_not_accept_edited_problem(monkeypatch):
    import atcoder_env.reviewed_constraints as registry

    english = BeautifulSoup("<div>Original constraint</div>", "html.parser").div
    item = dict(catalog()["abc485_b"], statement_sha256=statement_digest(english))
    monkeypatch.setattr(registry, "catalog", lambda: {"abc485_b": item})
    url = "https://atcoder.jp/contests/abc485/tasks/abc485_b"
    assert lookup(english, url, max_size=12) is not None
    changed = BeautifulSoup("<div>Changed constraint</div>", "html.parser").div
    assert lookup(changed, url, max_size=12) is None


def test_expression_interpreter_is_not_python_eval():
    with pytest.raises(ValueError):
        evaluate("__import__('os').system('echo unsafe')", {})
    assert evaluate("min(N*2, 100)-1", {"N": 12}) == 23


@given(st.data())
@settings(max_examples=25, deadline=None)
def test_unique_points_and_query_variants(data):
    model = reviewed("abc482_c")
    lines = model.serialize(data.draw(model.strategy())).splitlines()
    h, w, n, q = map(int, lines[0].split())
    points = [tuple(map(int, x.split())) for x in lines[1 : n + 1]]
    assert len(set(points)) == n
    assert all(1 <= r <= h and 1 <= c <= w for r, c in points)
    queries = [tuple(map(int, x.split())) for x in lines[n + 1 :]]
    assert len(queries) == q
    for t, left, right in queries:
        assert t in (1, 2)
        assert 1 <= left <= right <= (h if t == 1 else w)


@given(st.data())
@settings(max_examples=25, deadline=None)
def test_aggregate_cubic_budget_and_simple_graph(data):
    model = reviewed("abc482_g")
    lines = iter(model.serialize(data.draw(model.strategy())).splitlines())
    t = int(next(lines))
    total_cube = total_m = 0
    for _ in range(t):
        n, m = map(int, next(lines).split())
        a = list(map(int, next(lines).split()))
        assert len(a) == n and all(1 <= x < n for x in a)
        assert sum(a) <= 500
        edges = [tuple(map(int, next(lines).split())) for _ in range(m)]
        assert len(set(edges)) == m
        assert all(1 <= u < v <= n for u, v in edges)
        total_cube += sum(a) ** 3
        total_m += m
    assert total_cube <= 500**3 and total_m <= 200000
    assert next(lines, None) is None


@given(st.data())
@settings(max_examples=25, deadline=None)
def test_tree_parity_and_connectedness(data):
    model = reviewed("abc479_e")
    n, *lines = model.serialize(data.draw(model.strategy())).splitlines()
    n = int(n)
    assert n >= 3 and n % 2 == 1 and len(lines) == n - 1
    parent = list(range(n + 1))

    def find(x):
        while x != parent[x]:
            x = parent[x]
        return x

    for row in lines:
        a, b = map(int, row.split())
        assert 1 <= a < b <= n
        a, b = find(a), find(b)
        assert a != b
        parent[a] = b
    assert len({find(x) for x in range(1, n + 1)}) == 1


@given(st.data())
@settings(max_examples=25, deadline=None)
def test_modular_sum_and_permutation_prefix(data):
    model = reviewed("abc480_f")
    header, values = model.serialize(data.draw(model.strategy())).splitlines()
    n, m = map(int, header.split())
    a = list(map(int, values.split()))
    assert len(a) == n and sum(a) % m == 0 and all(1 <= x <= 10**9 for x in a)
    model = reviewed("abc478_f")
    n, values = model.serialize(data.draw(model.strategy())).splitlines()
    a = list(map(int, values.split()))
    assert a[0] == 1 and sorted(a) == list(range(1, int(n) + 1))
