"""Standalone examples for the optional non-AI generation libraries.

Run this script to print a seeded boundary-biased integer and an unweighted tree.
These helpers do not preserve Hypothesis shrinking by themselves. For automatic
shrinking, describe the input constraints with a Hypothesis strategy instead.
"""

import random

import onlinejudge_random
from cyaron import Edge, Graph


def boundary_integer(seed: int, lower: int = 1, upper: int = 200_000) -> int:
    if lower > upper:
        raise ValueError("lower must not exceed upper.")
    return onlinejudge_random.randint(lower, upper, r=random.Random(seed))


def cyaron_tree_text(n: int, seed: int) -> str:
    if n < 1:
        raise ValueError("Tree size must be positive.")
    # CYaRon uses the module-level random generator. Restore its state so the
    # example does not perturb another generator running in this process.
    state = random.getstate()
    try:
        random.seed(seed)
        tree = Graph.tree(n)
        edges = tree.to_str(output=Edge.unweighted_edge)
    finally:
        random.setstate(state)
    return f"{n}\n" + (f"{edges}\n" if edges else "")


if __name__ == "__main__":
    print(boundary_integer(seed=42))
    print(cyaron_tree_text(n=5, seed=42), end="")
