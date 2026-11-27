"""Small valid pair-sum inputs for differential tests and larger benchmarks."""

import random

from hypothesis import strategies as st


def strategy():
    return st.tuples(
        st.integers(min_value=-20, max_value=20),
        st.lists(st.integers(-10, 10), min_size=1, max_size=30),
    )


def serialize(case: tuple[int, list[int]]) -> str:
    target, values = case
    return f"{len(values)} {target}\n{' '.join(map(str, values))}\n"


def edge_cases() -> list[tuple[int, list[int]]]:
    return [
        (0, [0]),
        (20, [10, 10]),
        (-20, [-10, -10]),
        (0, [-10, 10]),
        (0, [0] * 30),
        (1, [0, 1] * 15),
        (20, list(range(-10, 11))),
    ]


def benchmark_case(size: int, seed: int) -> tuple[int, list[int]]:
    if size < 1:
        raise ValueError("Array size must be positive.")
    rng = random.Random(seed)
    return 0, [rng.randint(-10, 10) for _ in range(size)]
