# AI-assisted reusable example source:
# https://github.com/kitamura-tetsuo/atcoder-environment
"""Check every distinct index pair in O(n squared) time."""

import sys


def solve(data: str) -> str:
    numbers = list(map(int, data.split()))
    n, target = numbers[:2]
    values = numbers[2:]
    if len(values) != n:
        raise ValueError("Expected exactly n array elements.")
    answer = sum(
        values[left] + values[right] == target for left in range(n) for right in range(left + 1, n)
    )
    return f"{answer}\n"


if __name__ == "__main__":
    sys.stdout.write(solve(sys.stdin.read()))
