# AI-assisted reusable example source:
# https://github.com/kitamura-tetsuo/atcoder-environment
"""Count index pairs i < j whose values sum to the target in O(n) time."""

import sys
from collections import Counter


def solve(data: str) -> str:
    numbers = list(map(int, data.split()))
    n, target = numbers[:2]
    values = numbers[2:]
    if len(values) != n:
        raise ValueError("Expected exactly n array elements.")
    seen: Counter[int] = Counter()
    answer = 0
    for value in values:
        answer += seen[target - value]
        seen[value] += 1
    return f"{answer}\n"


if __name__ == "__main__":
    sys.stdout.write(solve(sys.stdin.read()))
