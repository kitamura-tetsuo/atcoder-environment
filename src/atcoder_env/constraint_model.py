"""A narrow, validated model for one integer-array input format.

Only constant bounds and an exact reference to the size variable are supported.
Unsupported expressions are rejected instead of weakening the input constraints.
"""

from __future__ import annotations

import json
import math
import random
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hypothesis import strategies as st

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")

BENCHMARK_PATTERNS = (
    "default",
    "balanced",
    "alternating",
    "blocks",
    "palette",
    "distinct",
    "sorted",
    "reversed",
    "random",
    "constant",
    "constant_max",
)


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

    def _validate_size(self, size: int, *, allow_out_of_range: bool = False) -> None:
        if allow_out_of_range and type(size) is int and size > 0:
            return
        if type(size) is not int or not self.size_min <= size <= self.size_max:
            raise ValueError(
                f"{self.size_name} must be an integer in the official range "
                f"{self.size_min}..{self.size_max}; got {size!r}"
            )

    def validate_case(self, case: list[int], *, allow_out_of_range: bool = False) -> None:
        """Reject invalid inputs before serializing them for a solution."""
        if not isinstance(case, list):
            raise ValueError("An integer-array case must be a list")
        self._validate_size(len(case), allow_out_of_range=allow_out_of_range)
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

    def serialize(self, case: list[int], *, allow_out_of_range: bool = False) -> str:
        """Serialize validated inputs; only opt-in benchmarks may exceed size bounds."""
        self.validate_case(case, allow_out_of_range=allow_out_of_range)
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

    def benchmark_patterns(self, size: int, *, allow_out_of_range: bool = False) -> tuple[str, ...]:
        """List feasible distributions spanning order, skew, and cardinality.

        These are representative adversarial families, not a proof that every
        possible solution's worst case is covered. Invalid families are omitted
        instead of silently generating a different distribution.
        """
        self._validate_size(size, allow_out_of_range=allow_out_of_range)
        lower, upper = self._bounds(size)
        return tuple(
            pattern
            for pattern in BENCHMARK_PATTERNS
            if not (pattern == "distinct" and upper - lower + 1 < size)
            and not (pattern in {"constant", "constant_max"} and self.min_distinct > 1)
        )

    def benchmark_case(
        self,
        size: int,
        seed: int,
        *,
        pattern: str = "default",
        allow_out_of_range: bool = False,
    ) -> list[int]:
        """Build a reproducible distribution, with opt-in size extrapolation."""
        return self._benchmark_case(
            size, seed, pattern=pattern, allow_out_of_range=allow_out_of_range
        )

    def _benchmark_case(
        self,
        size: int,
        seed: int,
        *,
        pattern: str,
        cardinality: int | None = None,
        span: int | None = None,
        allow_out_of_range: bool = False,
    ) -> list[int]:
        self._validate_size(size, allow_out_of_range=allow_out_of_range)
        if type(seed) is not int:
            raise ValueError("seed must be an integer, excluding booleans")
        if pattern not in BENCHMARK_PATTERNS:
            raise ValueError(f"Unknown benchmark pattern: {pattern}")
        lower, upper = self._bounds(size)
        if span is not None:
            upper = lower + span
        width = upper - lower + 1
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
        elif pattern in {"blocks", "palette", "sorted", "reversed"}:
            maximum = min(size, width)
            count = cardinality if cardinality is not None else maximum
            if cardinality is None and pattern == "blocks":
                count = min(maximum, max(2, math.isqrt(size)))
            palette = (
                [lower]
                if count == 1
                else [lower + index * (upper - lower) // (count - 1) for index in range(count)]
            )
            if pattern == "palette":
                # A repeated high-cardinality cycle complements long equal runs.
                values = [palette[index % count] for index in range(size)]
            else:
                values = [palette[index * count // size] for index in range(size)]
                if pattern == "reversed":
                    values.reverse()
        elif pattern == "distinct":
            if width < size:
                raise ValueError("The value range does not allow an all-distinct input")
            start = rng.randint(lower, upper - size + 1)
            values = list(range(start, start + size))
            rng.shuffle(values)
        elif pattern == "random":
            values = [rng.randint(lower, upper) for _ in range(size)]
            if self.min_distinct == 2 and all(value == values[0] for value in values):
                values[-1] = upper if values[0] != upper else lower
        else:
            if self.min_distinct > 1:
                raise ValueError(
                    "A constant pattern violates the minimum distinct-value constraint"
                )
            values = [lower if pattern == "constant" else upper] * size
        self.validate_case(values, allow_out_of_range=allow_out_of_range)
        return values

    def benchmark_strategy(
        self,
        max_size: int,
        seed: int,
        *,
        pattern: str = "default",
        size_only: bool = False,
        allow_out_of_range: bool = False,
    ):
        """Shrink resource cases using compact, full-size distribution parameters.

        Drawing each element independently would exhaust Hypothesis's choice
        budget at large N. Instead, shrink N, cardinality, value span, and a seed
        offset before constructing the list. The unmodified distribution at the
        original seed remains representable, including at max_size. A bounded
        search need not rediscover it, so callers must retain the initial case.
        A preliminary size_only pass preserves the measured seed and distribution.
        """
        self._validate_size(max_size, allow_out_of_range=allow_out_of_range)
        if type(seed) is not int:
            raise ValueError("seed must be an integer, excluding booleans")
        if pattern not in self.benchmark_patterns(max_size, allow_out_of_range=allow_out_of_range):
            raise ValueError(f"Benchmark pattern {pattern!r} is infeasible at size {max_size}")
        minimum = min(self.size_min, max_size) if allow_out_of_range else self.size_min
        if size_only:
            return st.integers(minimum, max_size).map(
                lambda size: self.benchmark_case(
                    size, seed, pattern=pattern, allow_out_of_range=allow_out_of_range
                )
            )

        @st.composite
        def cases(draw):
            size = draw(st.integers(minimum, max_size))
            lower, upper = self._bounds(size)
            maximum = min(size, upper - lower + 1)
            count = None
            if pattern in {"blocks", "palette", "sorted", "reversed"}:
                if pattern == "blocks":
                    maximum = min(maximum, max(2, math.isqrt(size)))
                count = draw(st.integers(self.min_distinct, maximum))
            minimum_span = max(self.min_distinct - 1, (count or 1) - 1)
            if pattern == "distinct":
                minimum_span = size - 1
            # Keep the shrinking target independent of N; drawing an absolute
            # span with a moving lower bound can prevent all-distinct N shrinking.
            span = minimum_span + draw(st.integers(0, upper - lower - minimum_span))
            variant_seed = seed
            if pattern in {"default", "balanced", "alternating", "distinct", "random"}:
                variant_seed ^= draw(st.integers(0, 65_535))
            return self._benchmark_case(
                size,
                variant_seed,
                pattern=pattern,
                cardinality=count,
                span=span,
                allow_out_of_range=allow_out_of_range,
            )

        return cases()

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
