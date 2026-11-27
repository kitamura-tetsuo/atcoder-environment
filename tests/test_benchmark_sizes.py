"""Keep default benchmarks within official bounds, including the largest input."""

from __future__ import annotations

from pathlib import Path

import pytest

from atcoder_env.benchmark_sizes import choose_benchmark_sizes
from atcoder_env.constraint_model import IntegerArrayConstraints


def write_constraints(
    root: Path, minimum: int = 2, maximum: int = 300_000, stress_cap: int = 30
) -> None:
    constraints = IntegerArrayConstraints(
        size_name="N",
        size_min=minimum,
        size_max=maximum,
        values_name="A",
        values_min=0,
        values_max=1,
        stress_max_size=stress_cap,
    )
    (root / "constraints.toml").write_text(constraints.to_toml(), encoding="utf-8")


def test_default_includes_official_maximum_despite_small_stress_cap(tmp_path):
    write_constraints(tmp_path)
    expected = [2, 10, 100, 1_000, 10_000, 100_000, 300_000]
    assert choose_benchmark_sizes(tmp_path) == expected
    write_constraints(tmp_path, stress_cap=2)
    assert choose_benchmark_sizes(tmp_path) == expected


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [
        (1, 1, [1]),
        (1, 10, [1, 10]),
        (2, 7, [2, 7]),
        (2, 100, [2, 10, 100]),
        (9, 11, [9, 10, 11]),
        (10, 100, [10, 100]),
        (11, 99, [11, 99]),
        (50, 500, [50, 100, 500]),
        (100, 100, [100]),
        (2, 2_000, [2, 10, 100, 1_000, 2_000]),
        (2, 1_000_000, [2, 10, 100, 1_000, 10_000, 100_000, 1_000_000]),
    ],
)
def test_automatic_sizes_cover_singletons_decades_and_narrow_ranges(
    tmp_path, minimum, maximum, expected
):
    write_constraints(tmp_path, minimum, maximum, stress_cap=max(30, minimum))
    assert choose_benchmark_sizes(tmp_path) == expected


def test_large_integer_bounds_do_not_require_enumerating_input_sizes(tmp_path):
    write_constraints(tmp_path, 2, 10**18)
    sizes = choose_benchmark_sizes(tmp_path)
    assert sizes == [2, *(10**exponent for exponent in range(1, 19))]


def test_explicit_sizes_keep_requested_order_without_adding_defaults(tmp_path):
    write_constraints(tmp_path)
    requested = [300_000, 30, 2]
    actual = choose_benchmark_sizes(tmp_path, requested)
    assert actual == requested
    assert actual is not requested


@pytest.mark.parametrize("requested", [[1], [300_001], [2, 30, 300_001]])
def test_any_explicit_size_outside_official_bounds_is_rejected(tmp_path, requested):
    write_constraints(tmp_path)
    with pytest.raises(ValueError, match="outside the official N range 2..300000"):
        choose_benchmark_sizes(tmp_path, requested)


@pytest.mark.parametrize("requested", [[], [0], [-1], [True], [2.0], ["2"], [2, 2], (2, 3)])
def test_invalid_explicit_sizes_are_rejected_even_without_constraints(tmp_path, requested):
    with pytest.raises(ValueError, match="Benchmark sizes must"):
        choose_benchmark_sizes(tmp_path, requested)


def test_missing_constraints_keep_generic_defaults_and_explicit_override(tmp_path):
    assert choose_benchmark_sizes(tmp_path) == [100, 1_000, 10_000]
    assert choose_benchmark_sizes(tmp_path, [3, 7]) == [3, 7]
    mutated = choose_benchmark_sizes(tmp_path)
    mutated.append(123)
    assert choose_benchmark_sizes(tmp_path) == [100, 1_000, 10_000]


@pytest.mark.parametrize("explicit", [None, [2]])
@pytest.mark.parametrize(
    "content",
    [
        "[size\n",
        "version = 2\n[size]\nmin = 2\nmax = 300000\n",
        "version = 1\n[size]\nmin = 2\nmax = 300000\n",
        'version = 1\n[size]\nname = "N"\nmin = true\nmax = 300000\n'
        '[values]\nname = "A"\nmin = 0\nmax = 1\n',
    ],
)
def test_malformed_or_unsupported_constraints_never_fall_back(tmp_path, explicit, content):
    (tmp_path / "constraints.toml").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid .*constraints.toml for benchmark sizes"):
        choose_benchmark_sizes(tmp_path, explicit)
