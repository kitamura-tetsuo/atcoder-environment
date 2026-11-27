"""Editable example constraints: one nonempty integer array.

Change the bounds, input shape, and edge cases to match the selected problem.
The solver and oracle receive only serialize(case), not Python objects.
"""

import random

from hypothesis import strategies as st


def strategy():
    # Keep inputs small enough for the brute-force reference solution.
    return st.lists(st.integers(-100, 100), min_size=1, max_size=30)


def serialize(case: list[int]) -> str:
    return f"{len(case)}\n{' '.join(map(str, case))}\n"


def edge_cases() -> list[list[int]]:
    return [[0], [-100], [100], [0] * 30, [-100, 100], list(range(30)), list(range(29, -1, -1))]


def benchmark_case(size: int, seed: int) -> list[int]:
    if size < 1:
        raise ValueError("Array size must be positive.")
    rng = random.Random(seed)
    return [rng.randint(-100, 100) for _ in range(size)]
