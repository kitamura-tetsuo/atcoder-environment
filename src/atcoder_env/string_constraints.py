"""Validated finite-alphabet strings with the shared benchmark distributions."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hypothesis import strategies as st

from atcoder_env.constraint_model import IntegerArrayConstraints


@dataclass(frozen=True)
class StringConstraints:
    """Constraints for input consisting of N followed by one N-character string."""

    size_name: str
    size_min: int
    size_max: int
    string_name: str
    alphabet: str
    stress_max_size: int = 30
    url: str = ""

    def __post_init__(self) -> None:
        for name in ("size_name", "string_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value) is None:
                raise ValueError(f"{name} must be an ASCII identifier starting with a letter")
        if self.size_name == self.string_name:
            raise ValueError("Size and string names must be different")
        if not isinstance(self.alphabet, str) or not self.alphabet:
            raise ValueError("alphabet must be a nonempty string of allowed characters")
        if len(set(self.alphabet)) != len(self.alphabet):
            raise ValueError("alphabet must not repeat characters")
        if any(not "!" <= character <= "~" for character in self.alphabet):
            raise ValueError("alphabet characters must be printable ASCII without whitespace")
        # Reuse the existing size, testing-cap, and URL validation unchanged.
        self._integer_model()

    def _integer_model(self) -> IntegerArrayConstraints:
        return IntegerArrayConstraints(
            size_name=self.size_name,
            size_min=self.size_min,
            size_max=self.size_max,
            values_name=self.string_name,
            values_min=0,
            values_max=len(self.alphabet) - 1,
            stress_max_size=self.stress_max_size,
            url=self.url,
        )

    def _decode(self, indices: list[int]) -> str:
        # Keep sorted/reversed and constant_max meaningful for any alphabet order.
        alphabet = sorted(self.alphabet)
        return "".join(alphabet[index] for index in indices)

    @property
    def effective_stress_max_size(self) -> int:
        return min(self.stress_max_size, self.size_max)

    def validate_case(self, case: str, *, allow_out_of_range: bool = False) -> None:
        if not isinstance(case, str):
            raise ValueError("A string case must be a str")
        if not (
            len(case) > 0 if allow_out_of_range else self.size_min <= len(case) <= self.size_max
        ):
            raise ValueError(
                f"{self.string_name} length must be in the official "
                f"{self.size_name} range {self.size_min}..{self.size_max}; got {len(case)}"
            )
        allowed = set(self.alphabet)
        if any(character not in allowed for character in case):
            raise ValueError(
                f"{self.string_name} contains a character outside alphabet {self.alphabet!r}"
            )

    def strategy(self):
        """Generate and shrink small strings using only the explicit alphabet."""
        return st.text(
            alphabet=self.alphabet,
            min_size=self.size_min,
            max_size=self.effective_stress_max_size,
        )

    def serialize(self, case: str, *, allow_out_of_range: bool = False) -> str:
        self.validate_case(case, allow_out_of_range=allow_out_of_range)
        return f"{len(case)}\n{case}\n"

    def edge_cases(self) -> list[str]:
        cases = [self._decode(case) for case in self._integer_model().edge_cases()]
        for size in {self.size_min, self.effective_stress_max_size}:
            cases.extend(character * size for character in self.alphabet)
            cycle = (self.alphabet * ((size + len(self.alphabet) - 1) // len(self.alphabet)))[:size]
            cases.extend((cycle, cycle[::-1]))
        return list(dict.fromkeys(cases))

    def benchmark_patterns(self, size: int, *, allow_out_of_range: bool = False) -> tuple[str, ...]:
        return self._integer_model().benchmark_patterns(size, allow_out_of_range=allow_out_of_range)

    def benchmark_case(
        self,
        size: int,
        seed: int,
        *,
        pattern: str = "default",
        allow_out_of_range: bool = False,
    ) -> str:
        indices = self._integer_model().benchmark_case(
            size, seed, pattern=pattern, allow_out_of_range=allow_out_of_range
        )
        return self._decode(indices)

    def benchmark_strategy(
        self,
        max_size: int,
        seed: int,
        *,
        pattern: str = "default",
        size_only: bool = False,
        allow_out_of_range: bool = False,
    ):
        """Retain compact distribution shrinking for full-size resource failures."""
        return (
            self._integer_model()
            .benchmark_strategy(
                max_size,
                seed,
                pattern=pattern,
                size_only=size_only,
                allow_out_of_range=allow_out_of_range,
            )
            .map(self._decode)
        )

    def to_toml(self) -> str:
        return (
            "version = 1\n\n"
            "[problem]\n"
            f"url = {json.dumps(self.url)}\n\n"
            "[size]\n"
            f"name = {json.dumps(self.size_name)}\n"
            f"min = {self.size_min}\n"
            f"max = {self.size_max}\n\n"
            "[string]\n"
            f"name = {json.dumps(self.string_name)}\n"
            f"alphabet = {json.dumps(self.alphabet)}\n\n"
            "[testing]\n"
            f"max_size = {self.stress_max_size}\n"
        )

    @classmethod
    def from_toml(cls, path: Path) -> StringConstraints:
        """Load the complete string schema without ignoring additional conditions."""
        with Path(path).open("rb") as stream:
            data = tomllib.load(stream)
        unknown = data.keys() - {"version", "problem", "size", "string", "testing"}
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
        string = table("string", {"name", "alphabet"}, {"name", "alphabet"})
        testing = table("testing", {"max_size"}, set())
        return cls(
            size_name=size["name"],
            size_min=size["min"],
            size_max=size["max"],
            string_name=string["name"],
            alphabet=string["alphabet"],
            stress_max_size=testing.get("max_size", 30),
            url=problem.get("url", ""),
        )
