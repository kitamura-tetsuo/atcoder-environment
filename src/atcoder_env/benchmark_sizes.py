"""Choose benchmark sizes from official bounds rather than stress-test limits."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from atcoder_env.constraint_io import load_constraints

_DEFAULT_SIZES = (100, 1_000, 10_000)


def choose_benchmark_sizes(root: Path, explicit_sizes: list[int] | None = None) -> list[int]:
    """Allow explicit positive sizes, or include official bounds and each decade.

    A missing constraints file retains the generic workspace defaults. A present
    but invalid file must fail instead of silently benchmarking the wrong range.
    """
    if explicit_sizes is not None:
        if not isinstance(explicit_sizes, list) or not explicit_sizes:
            raise ValueError("Benchmark sizes must be a nonempty list of positive integers")
        if any(type(size) is not int or size <= 0 for size in explicit_sizes):
            raise ValueError("Benchmark sizes must be positive integers, excluding booleans")
        if len(set(explicit_sizes)) != len(explicit_sizes):
            raise ValueError("Benchmark sizes must be unique")

    constraints_path = root / "constraints.toml"
    if not constraints_path.exists():
        return list(_DEFAULT_SIZES) if explicit_sizes is None else list(explicit_sizes)
    try:
        constraints = load_constraints(constraints_path)
    except ValueError as error:
        raise ValueError(f"Invalid {constraints_path} for benchmark sizes: {error}") from error

    minimum, maximum = constraints.size_min, constraints.size_max
    if explicit_sizes is not None:
        for size in explicit_sizes:
            if not minimum <= size <= maximum:
                logger.warning(
                    "Benchmark size {} is outside the official {} range {}..{}; "
                    "continuing because --sizes was explicitly provided",
                    size,
                    constraints.size_name,
                    minimum,
                    maximum,
                )
        return list(explicit_sizes)

    sizes = {minimum, maximum}
    power = 10
    while power < maximum:
        if power > minimum:
            sizes.add(power)
        power *= 10
    return sorted(sizes)
