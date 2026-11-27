"""Validate string generation, serialization, distributions, and shrinking."""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import find, given, settings

from atcoder_env.string_constraints import StringConstraints

STRING = StringConstraints(
    "N", 3, 300_000, "S", "ABC", url="https://atcoder.jp/contests/abc485/tasks/abc485_d"
)


@settings(max_examples=50, deadline=None)
@given(STRING.strategy())
def test_small_strategy_emits_only_allowed_characters_and_serializes_two_lines(case):
    assert isinstance(case, str)
    assert 3 <= len(case) <= 30
    assert set(case) <= {"A", "B", "C"}
    assert STRING.serialize(case) == f"{len(case)}\n{case}\n"


def test_small_strategy_shrinks_without_losing_length_or_alphabet_constraints():
    case = find(
        STRING.strategy(),
        lambda value: len(value) >= 7 and "B" in value,
        settings=settings(max_examples=200, deadline=None, database=None),
    )
    assert len(case) == 7
    assert "B" in case
    STRING.validate_case(case)


def test_edge_cases_cover_every_constant_and_full_alphabet_cycles():
    cases = STRING.edge_cases()
    assert len(cases) == len(set(cases))
    assert {len(case) for case in cases} == {3, 4, 30}
    for case in cases:
        STRING.validate_case(case)
    for character in "ABC":
        assert character * 3 in cases
        assert character * 30 in cases
    assert "ABC" * 10 in cases
    assert "CBA" * 10 in cases


@pytest.mark.parametrize("size", [3, 100])
def test_all_feasible_distributions_preserve_string_constraints_and_seed(size):
    patterns = STRING.benchmark_patterns(size)
    assert ("distinct" in patterns) == (size == 3)
    assert {"palette", "blocks", "random", "constant", "constant_max"}.issubset(patterns)
    for pattern in patterns:
        case = STRING.benchmark_case(size, 29, pattern=pattern)
        STRING.validate_case(case)
        assert len(case) == size
        assert case == STRING.benchmark_case(size, 29, pattern=pattern)


def test_official_maximum_generates_a_string_containing_every_allowed_letter():
    case = STRING.benchmark_case(300_000, 2, pattern="palette")
    assert case == "ABC" * 100_000
    assert STRING.serialize(case).splitlines() == ["300000", case]


def test_distribution_order_uses_character_order_even_for_unordered_alphabet():
    model = replace(STRING, alphabet="CBA")
    ascending = model.benchmark_case(12, 0, pattern="sorted")
    descending = model.benchmark_case(12, 0, pattern="reversed")
    assert ascending == "AAAABBBBCCCC"
    assert descending == ascending[::-1]
    assert model.benchmark_case(12, 0, pattern="constant_max") == "C" * 12


def test_compact_benchmark_strategy_can_shrink_beyond_stress_size_cap():
    case = find(
        STRING.benchmark_strategy(1_000, 17, pattern="palette"),
        lambda value: len(value) >= 70 and "B" in value,
        settings=settings(max_examples=200, deadline=None, database=None),
    )
    assert len(case) == 70
    assert "B" in case
    STRING.validate_case(case)
    fixed = find(
        STRING.benchmark_strategy(1_000, 17, pattern="random", size_only=True),
        lambda value: len(value) >= 70,
        settings=settings(max_examples=200, deadline=None, database=None),
    )
    assert fixed == STRING.benchmark_case(70, 17, pattern="random")


def test_single_character_alphabet_remains_valid():
    model = StringConstraints("length", 1, 10, "word", "x")
    assert all(case == "x" * len(case) for case in model.edge_cases())
    assert "distinct" not in model.benchmark_patterns(10)
    for pattern in model.benchmark_patterns(10):
        assert model.benchmark_case(10, 1, pattern=pattern) == "x" * 10


@pytest.mark.parametrize(
    "changes",
    [
        {"size_name": "N+1"},
        {"string_name": "S[0]"},
        {"string_name": "N"},
        {"size_min": True},
        {"size_max": 30.0},
        {"size_min": 0},
        {"size_max": 2},
        {"stress_max_size": 2},
        {"stress_max_size": True},
        {"url": None},
        {"alphabet": ""},
        {"alphabet": "ABA"},
        {"alphabet": "A B"},
        {"alphabet": "A\nB"},
        {"alphabet": "A\tB"},
        {"alphabet": "A\x7f"},
        {"alphabet": "Aé"},
        {"alphabet": ["A", "B"]},
    ],
)
def test_invalid_models_are_rejected(changes):
    with pytest.raises(ValueError):
        replace(STRING, **changes)


@pytest.mark.parametrize("case", ["", "AB", "ABX", "A B", "AB\n", ["A", "B", "C"], 123])
def test_serialization_rejects_wrong_length_type_or_characters(case):
    with pytest.raises(ValueError):
        STRING.serialize(case)


def test_large_case_and_infeasible_benchmark_fail():
    with pytest.raises(ValueError, match="official"):
        replace(STRING, size_max=4).serialize("ABCAB")
    with pytest.raises(ValueError, match="all-distinct"):
        STRING.benchmark_case(4, 0, pattern="distinct")
    with pytest.raises(ValueError, match="official"):
        STRING.benchmark_case(300_001, 0)
