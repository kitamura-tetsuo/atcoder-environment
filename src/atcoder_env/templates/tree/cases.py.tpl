"""Editable example constraints: an unweighted tree with 1-based vertex IDs.

Choosing one earlier parent for each new vertex always produces a valid tree.
Relabeling covers arbitrary vertex labels, including while Hypothesis shrinks.
"""

import random

from hypothesis import strategies as st


@st.composite
def strategy(draw):
    n = draw(st.integers(min_value=1, max_value=20))
    labels = draw(st.permutations(tuple(range(1, n + 1))))
    edges = [
        (labels[draw(st.integers(0, vertex - 1))], labels[vertex])
        for vertex in range(1, n)
    ]
    return n, edges


def serialize(case: tuple[int, list[tuple[int, int]]]) -> str:
    n, edges = case
    return f"{n}\n" + "".join(f"{left} {right}\n" for left, right in edges)


def edge_cases() -> list[tuple[int, list[tuple[int, int]]]]:
    n = 20
    return [
        (1, []),
        (2, [(1, 2)]),
        (n, [(vertex - 1, vertex) for vertex in range(2, n + 1)]),
        (n, [(1, vertex) for vertex in range(2, n + 1)]),
        (n, [(n, vertex) for vertex in range(1, n)]),
    ]


def benchmark_case(size: int, seed: int) -> tuple[int, list[tuple[int, int]]]:
    if size < 1:
        raise ValueError("Tree size must be positive.")
    rng = random.Random(seed)
    labels = list(range(1, size + 1))
    rng.shuffle(labels)
    edges = [(labels[rng.randrange(vertex)], labels[vertex]) for vertex in range(1, size)]
    return size, edges
