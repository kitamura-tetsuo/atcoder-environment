# AI-assisted reusable example source:
# https://github.com/kitamura-tetsuo/atcoder-environment
"""Intentionally wrong: a set loses the multiplicity of previous values."""

import sys


def solve(data: str) -> str:
    numbers = list(map(int, data.split()))
    n, target = numbers[:2]
    values = numbers[2:]
    if len(values) != n:
        raise ValueError("Expected exactly n array elements.")
    seen: set[int] = set()
    answer = 0
    for value in values:
        # This is the deliberate bug used to demonstrate shrinking.
        answer += int(target - value in seen)
        seen.add(value)
    return f"{answer}\n"


if __name__ == "__main__":
    sys.stdout.write(solve(sys.stdin.read()))
