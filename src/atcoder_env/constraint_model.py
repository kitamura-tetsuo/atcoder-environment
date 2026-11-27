"""A narrow, validated model for one integer-array input format.

Only constant bounds and an exact reference to the size variable are supported.
Unsupported expressions are rejected instead of weakening the input constraints.
"""

from __future__ import annotations

import json
import random
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hypothesis import strategies as st

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class IntegerArrayConstraints:
    """Constraints for input consisting of N followed by exactly N integers."""

    size_name: str
    size_min: int
    size_max: int
    values_name: str
    values_min: int | str
    values_max: int | str
    min_distinct: int = 1
    stress_max_size: int = 30
    url: str = ""

    def __post_init__(self) -> None:
        for name in ("size_name", "values_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be an ASCII identifier starting with a letter")
        if self.size_name == self.values_name:
            raise ValueError("Size and array names must be different")
        for name in ("size_min", "size_max", "min_distinct", "stress_max_size"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer, excluding booleans")
        if self.size_min < 1 or self.size_max < self.size_min:
            raise ValueError("Size bounds must satisfy 1 <= size_min <= size_max")
        if self.min_distinct not in (1, 2):
            raise ValueError("Only min_distinct values 1 and 2 are supported")
        if self.size_min < self.min_distinct:
            raise ValueError("size_min must allow at least min_distinct elements")
        if self.stress_max_size < self.size_min:
            raise ValueError("stress_max_size must be at least size_min; increase the testing cap")
        if not isinstance(self.url, str):
            raise ValueError("url must be a string")
        for name in ("values_min", "values_max"):
            bound = getattr(self, name)
            if not (type(bound) is int or (isinstance(bound, str) and bound == self.size_name)):
                raise ValueError(f"{name} must be an integer or exactly {self.size_name!r}")

        # Each bound is constant or N, so domain width is affine in N. Checking
        # both endpoints proves feasibility throughout the allowed size interval.
        for n in {self.size_min, self.size_max}:
            lower, upper = self._bounds(n)
            if upper - lower + 1 < self.min_distinct:
                raise ValueError(
                    f"Value bounds at {self.size_name}={n} cannot supply "
                    f"{self.min_distinct} distinct value(s): {lower}..{upper}"
                )

    @property
    def effective_stress_max_size(self) -> int:
        """The official maximum also limits small differential-test inputs."""
        return min(self.stress_max_size, self.size_max)

    def _bounds(self, size: int) -> tuple[int, int]:
        lower = size if isinstance(self.values_min, str) else self.values_min
        upper = size if isinstance(self.values_max, str) else self.values_max
        return lower, upper

    def _validate_size(self, size: int) -> None:
        if type(size) is not int or not self.size_min <= size <= self.size_max:
            raise ValueError(
                f"{self.size_name} must be an integer in the official range "
                f"{self.size_min}..{self.size_max}; got {size!r}"
            )

    def validate_case(self, case: list[int]) -> None:
        """Reject invalid inputs before serializing them for a solution."""
        if not isinstance(case, list):
            raise ValueError("An integer-array case must be a list")
        self._validate_size(len(case))
        lower, upper = self._bounds(len(case))
        for index, value in enumerate(case, 1):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(
                    f"{self.values_name}[{index}] must be an integer in {lower}..{upper}; "
                    f"got {value!r}"
                )
        if self.min_distinct == 2 and not any(value != case[0] for value in case[1:]):
            raise ValueError(f"{self.values_name} must contain at least two distinct values")

    def strategy(self):
        """Generate valid small cases while retaining Hypothesis shrinking.

        The distinctness filter imposes no special positions, so a lone outlier
        may appear at any index and move as the counterexample is shrunk.
        """

        @st.composite
        def arrays(draw):
            size = draw(st.integers(self.size_min, self.effective_stress_max_size))
            lower, upper = self._bounds(size)
            elements = st.lists(st.integers(lower, upper), min_size=size, max_size=size)
            if self.min_distinct == 2:
                elements = elements.filter(lambda values: any(x != values[0] for x in values[1:]))
            return draw(elements)

        return arrays()

    def serialize(self, case: list[int]) -> str:
        """Serialize only cases satisfying every modeled official constraint."""
        self.validate_case(case)
        return f"{len(case)}\n{' '.join(map(str, case))}\n"

    def edge_cases(self) -> list[list[int]]:
        """Cover structured small inputs without overwhelming a brute-force oracle."""
        maximum = self.effective_stress_max_size
        sizes = sorted({self.size_min, min(self.size_min + 1, maximum), maximum})
        cases: list[list[int]] = []
        seen: set[tuple[int, ...]] = set()
        for size in sizes:
            lower, upper = self._bounds(size)
            candidates = [[lower] * size, [upper] * size]
            if lower != upper:
                for common, outlier in ((lower, upper), (upper, lower)):
                    for position in {0, size // 2, size - 1}:
                        values = [common] * size
                        values[position] = outlier
                        candidates.append(values)
                alternating = [lower if index % 2 == 0 else upper for index in range(size)]
                candidates.extend((alternating, list(reversed(alternating))))
            if upper - lower + 1 >= size:
                ascending = list(range(lower, lower + size))
                candidates.extend((ascending, list(reversed(ascending))))
                upper_values = list(range(upper - size + 1, upper + 1))
                candidates.extend((upper_values, list(reversed(upper_values))))
            for case in candidates:
                if self.min_distinct == 2 and not any(value != case[0] for value in case[1:]):
                    continue
                key = tuple(case)
                if key not in seen:
                    self.validate_case(case)
                    seen.add(key)
                    cases.append(case)
        return cases

    def benchmark_case(self, size: int, seed: int, *, pattern: str = "default") -> list[int]:
        """Build a reproducible input distribution within the official constraints."""
        self._validate_size(size)
        if type(seed) is not int:
            raise ValueError("seed must be an integer, excluding booleans")
        lower, upper = self._bounds(size)
        rng = random.Random(seed)
        common, outlier = (lower, upper) if rng.randrange(2) == 0 else (upper, lower)
        if pattern == "default":
            values = [common] * size
            if size > 1 and common != outlier:
                values[rng.choice((0, size // 2, size - 1))] = outlier
        elif pattern == "balanced":
            values = [common] * (size // 2) + [outlier] * (size - size // 2)
        elif pattern == "alternating":
            values = [common if index % 2 == 0 else outlier for index in range(size)]
        elif pattern == "distinct":
            if upper - lower + 1 < size:
                raise ValueError("The value range does not allow an all-distinct input")
            start = rng.randint(lower, upper - size + 1)
            values = list(range(start, start + size))
            rng.shuffle(values)
        elif pattern == "random":
            values = [rng.randint(lower, upper) for _ in range(size)]
            if self.min_distinct == 2 and all(value == values[0] for value in values):
                values[-1] = upper if values[0] != upper else lower
        else:
            raise ValueError(f"Unknown benchmark pattern: {pattern}")
        self.validate_case(values)
        return values

    def to_toml(self) -> str:
        """Return the complete versioned, human-editable constraints document."""

        def quoted(value: str | int) -> str:
            return json.dumps(value, ensure_ascii=False)

        return (
            "version = 1\n\n"
            "[problem]\n"
            f"url = {quoted(self.url)}\n\n"
            "[size]\n"
            f"name = {quoted(self.size_name)}\n"
            f"min = {self.size_min}\n"
            f"max = {self.size_max}\n\n"
            "[values]\n"
            f"name = {quoted(self.values_name)}\n"
            f"min = {quoted(self.values_min)}\n"
            f"max = {quoted(self.values_max)}\n"
            f"min_distinct = {self.min_distinct}\n\n"
            "[testing]\n"
            f"max_size = {self.stress_max_size}\n"
        )

    @classmethod
    def from_toml(cls, path: Path) -> IntegerArrayConstraints:
        """Load only the supported schema; unknown fields never lose meaning silently."""
        with Path(path).open("rb") as stream:
            data = tomllib.load(stream)
        unknown = data.keys() - {"version", "problem", "size", "values", "testing"}
        if unknown:
            raise ValueError(f"Unknown constraints keys: {', '.join(sorted(unknown))}")
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("Constraints version must be the integer 1")

        def table(name: str, allowed: set[str], required: set[str]) -> dict[str, Any]:
            value = data.get(name, {})
            if not isinstance(value, dict):
                raise ValueError(f"[{name}] must be a table")
            extra = value.keys() - allowed
            missing = required - value.keys()
            if extra:
                raise ValueError(f"Unknown [{name}] keys: {', '.join(sorted(extra))}")
            if missing:
                raise ValueError(f"Missing [{name}] keys: {', '.join(sorted(missing))}")
            return value

        problem = table("problem", {"url"}, set())
        size = table("size", {"name", "min", "max"}, {"name", "min", "max"})
        values = table("values", {"name", "min", "max", "min_distinct"}, {"name", "min", "max"})
        testing = table("testing", {"max_size"}, set())
        return cls(
            size_name=size["name"],
            size_min=size["min"],
            size_max=size["max"],
            values_name=values["name"],
            values_min=values["min"],
            values_max=values["max"],
            min_distinct=values.get("min_distinct", 1),
            stress_max_size=testing.get("max_size", 30),
            url=problem.get("url", ""),
        )
