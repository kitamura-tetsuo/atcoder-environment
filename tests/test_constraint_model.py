"""Validate modeled constraints across generation, shrinking, and persisted config."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import find, given, settings

from atcoder_env.constraint_model import IntegerArrayConstraints

DEPENDENT = IntegerArrayConstraints(
    size_name="N",
    size_min=2,
    size_max=300_000,
    values_name="C",
    values_min=1,
    values_max="N",
    min_distinct=2,
    url="https://atcoder.jp/contests/abc485/tasks/abc485_c",
)


def assert_valid_dependent(case):
    assert 2 <= len(case) <= 300_000
    assert all(type(value) is int and 1 <= value <= len(case) for value in case)
    assert len(set(case)) >= 2


@settings(max_examples=80, deadline=None)
@given(DEPENDENT.strategy())
def test_dependent_size_values_and_distinctness_always_hold(case):
    assert_valid_dependent(case)
    assert len(case) <= 30
    tokens = list(map(int, DEPENDENT.serialize(case).split()))
    assert tokens == [len(case), *case]


def test_hypothesis_shrinking_preserves_all_dependent_constraints():
    case = find(
        DEPENDENT.strategy(),
        lambda values: len(values) >= 5,
        settings=settings(max_examples=150, deadline=None, database=None),
    )
    assert len(case) == 5
    assert_valid_dependent(case)
    assert len(DEPENDENT.serialize(case).split()) == 6


@pytest.mark.parametrize("outlier_position", [0, 1, 2])
def test_distinctness_does_not_force_outlier_to_a_specific_position(outlier_position):
    spec = replace(DEPENDENT, size_min=3, size_max=3)

    def has_outlier_at(values):
        others = [value for index, value in enumerate(values) if index != outlier_position]
        return others[0] == others[1] and values[outlier_position] != others[0]

    case = find(
        spec.strategy(),
        has_outlier_at,
        settings=settings(max_examples=200, deadline=None, database=None),
    )
    assert has_outlier_at(case)
    assert_valid_dependent(case)


def test_edge_cases_include_small_boundaries_and_structured_adversaries():
    cases = DEPENDENT.edge_cases()
    assert cases
    assert len({tuple(case) for case in cases}) == len(cases)
    assert {len(case) for case in cases} == {2, 3, 30}
    for case in cases:
        assert_valid_dependent(case)
        DEPENDENT.serialize(case)
    for position in (0, 15, 29):
        expected = [1] * 30
        expected[position] = 30
        assert expected in cases
    assert [1, 30] * 15 in cases
    assert list(range(1, 31)) in cases
    assert list(range(30, 0, -1)) in cases
    assert [1] * 30 not in cases


def test_full_size_benchmark_is_valid_seeded_and_has_a_long_run():
    case = DEPENDENT.benchmark_case(300_000, seed=2026)
    assert_valid_dependent(case)
    assert len(case) == 300_000
    assert case == DEPENDENT.benchmark_case(300_000, seed=2026)
    assert set(case) == {1, 300_000}
    assert sorted((case.count(1), case.count(300_000))) == [1, 299_999]


@pytest.mark.parametrize("size", [0, 1, 300_001, True, 2.0])
def test_benchmark_rejects_sizes_outside_official_constraints(size):
    with pytest.raises(ValueError, match="official range"):
        DEPENDENT.benchmark_case(size, seed=0)


def test_constant_negative_bounds_and_singleton_domain_work():
    spec = IntegerArrayConstraints("n", 1, 10, "array", -4, -2)

    @settings(max_examples=30, deadline=None)
    @given(spec.strategy())
    def check(case):
        assert 1 <= len(case) <= 10
        assert all(-4 <= value <= -2 for value in case)
        spec.serialize(case)

    check()
    fixed = IntegerArrayConstraints("N", 1, 3, "A", "N", "N")
    for case in fixed.edge_cases():
        assert case == [len(case)] * len(case)
        fixed.serialize(case)
    assert fixed.benchmark_case(3, seed=4) == [3, 3, 3]


def test_stress_cap_never_silently_falls_below_official_minimum():
    with pytest.raises(ValueError, match="testing cap"):
        IntegerArrayConstraints("N", 50, 100, "A", 0, 1)
    spec = IntegerArrayConstraints("N", 50, 100, "A", 0, 1, stress_max_size=50)
    case = find(spec.strategy(), lambda _values: True, settings=settings(database=None))
    assert len(case) == 50
    assert {len(values) for values in spec.edge_cases()} == {50}
    small = replace(DEPENDENT, size_max=4)
    assert small.effective_stress_max_size == 4
    assert all(len(values) <= 4 for values in small.edge_cases())


@pytest.mark.parametrize(
    "changes",
    [
        {"size_name": "N+1"},
        {"size_name": "１"},
        {"values_name": "N"},
        {"size_min": True},
        {"size_max": 30.0},
        {"size_min": 0},
        {"size_min": 3, "size_max": 2},
        {"size_min": 1},
        {"stress_max_size": 1},
        {"stress_max_size": "30"},
        {"min_distinct": True},
        {"min_distinct": 3},
        {"values_min": False},
        {"values_max": "N-1"},
        {"values_max": "n"},
        {"values_max": 1},
        {"values_min": "N", "values_max": 20},
        {"values_min": "N", "values_max": "N"},
        {"url": None},
    ],
)
def test_ambiguous_impossible_and_wrongly_typed_models_are_rejected(changes):
    with pytest.raises(ValueError):
        replace(DEPENDENT, **changes)


@pytest.mark.parametrize("case", [[], [1], [1, 1], [0, 2], [1, 3], [True, 2], [1.0, 2], (1, 2)])
def test_serialize_rejects_invalid_cases_before_they_reach_a_solution(case):
    with pytest.raises(ValueError):
        DEPENDENT.serialize(case)


def write_constraints(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "constraints.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_toml_round_trip_preserves_constants_symbols_and_escaped_strings(tmp_path):
    spec = replace(DEPENDENT, url='https://example.test/?quote="value"&slash=\\')
    assert IntegerArrayConstraints.from_toml(write_constraints(tmp_path, spec.to_toml())) == spec
    constants = IntegerArrayConstraints("length", 1, 10, "values", -20, 40)
    assert (
        IntegerArrayConstraints.from_toml(write_constraints(tmp_path, constants.to_toml()))
        == constants
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("version = 1", 'version = "1"'),
        lambda text: text.replace("version = 1", "version = true"),
        lambda text: text.replace("version = 1", "version = 2"),
        lambda text: text.replace("version = 1", "version = 1\nunknown = 5"),
        lambda text: text.replace("[size]", "[size]\nunrecognized_bound = 2"),
        lambda text: text.replace("[values]", "[values]\nsorted = true"),
        lambda text: text.replace("[testing]", "[testing]\nfoo = true"),
        lambda text: text.replace("[problem]", "[problem]\nproblem_type = 'graph'"),
        lambda text: text.replace('name = "N"\n', ""),
        lambda text: text.replace('max = "N"', 'max = "N + 1"'),
        lambda text: text.replace("min_distinct = 2", "min_distinct = false"),
        lambda text: text.replace("max_size = 30", "max_size = 1"),
        lambda text: text + "\n[additional_constraints]\nconnected = true\n",
    ],
)
def test_toml_loader_never_ignores_unsupported_constraints(tmp_path, mutate):
    with pytest.raises(ValueError):
        IntegerArrayConstraints.from_toml(write_constraints(tmp_path, mutate(DEPENDENT.to_toml())))


@pytest.mark.parametrize("pattern", ["balanced", "alternating", "distinct", "random"])
@pytest.mark.parametrize("size", [2, 3, 1000])
def test_benchmark_patterns_are_valid_and_reproducible(pattern, size):
    values = DEPENDENT.benchmark_case(size, seed=5, pattern=pattern)
    assert len(values) == size
    assert_valid_dependent(values)
    assert values == DEPENDENT.benchmark_case(size, seed=5, pattern=pattern)
    if pattern in {"balanced", "alternating"}:
        assert sorted(values.count(x) for x in set(values)) == [size // 2, size - size // 2]
    if pattern == "alternating":
        assert all(a != b for a, b in zip(values, values[1:], strict=False))
    if pattern == "distinct":
        assert len(set(values)) == size


def test_distinct_pattern_requires_sufficient_domain():
    spec = IntegerArrayConstraints("N", 1, 10, "A", 1, 2)
    with pytest.raises(ValueError, match="all-distinct"):
        spec.benchmark_case(3, 0, pattern="distinct")
