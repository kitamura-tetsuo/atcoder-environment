"""Validated N M / M rows of endpoint-endpoint-label integer input."""

from __future__ import annotations

import json
import math
import random
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hypothesis import strategies as st

GRAPH_PATTERNS = (
    "default",
    "cycle",
    "star_out",
    "star_in",
    "hub_distinct",
    "hub_same",
    "hub_two",
    "parallel",
    "dense",
    "sparse",
    "random",
)

GraphCase = tuple[int, list[tuple[int, int, int]]]


@dataclass(frozen=True)
class GraphConstraints:
    """Labeled edges, no self-loops; parallel edges are permitted."""

    vertex_name: str
    vertex_min: int
    vertex_max: int
    edge_name: str
    edge_min: int
    edge_max: int
    source_name: str
    target_name: str
    label_name: str
    stress_max_size: int = 30
    url: str = ""

    def __post_init__(self):
        names = (
            self.vertex_name,
            self.edge_name,
            self.source_name,
            self.target_name,
            self.label_name,
        )
        if any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name)
            for name in names
        ):
            raise ValueError("Graph variable names must be ASCII identifiers")
        if len(set(names)) != len(names):
            raise ValueError("Graph variable names must be distinct")
        for field in ("vertex_min", "vertex_max", "edge_min", "edge_max", "stress_max_size"):
            if type(getattr(self, field)) is not int:
                raise ValueError(f"{field} must be an integer")
        if not 2 <= self.vertex_min <= self.vertex_max:
            raise ValueError("Vertex bounds must satisfy 2 <= min <= max")
        if not 1 <= self.edge_min <= self.edge_max:
            raise ValueError("Edge bounds must satisfy 1 <= min <= max")
        if self.stress_max_size < max(self.vertex_min, self.edge_min):
            raise ValueError("Stress max_size must allow both minimum vertex and edge counts")
        if not isinstance(self.url, str):
            raise ValueError("url must be a string")

    # Benchmark size denotes the repeated row count, not the number of vertices.
    @property
    def size_name(self):
        return self.edge_name

    @property
    def size_min(self):
        return self.edge_min

    @property
    def size_max(self):
        return self.edge_max

    @property
    def effective_stress_max_size(self):
        return min(self.stress_max_size, self.edge_max)

    def _check_size(self, size, allow_out_of_range):
        if (
            type(size) is not int
            or size < 1
            or (not allow_out_of_range and not self.edge_min <= size <= self.edge_max)
        ):
            raise ValueError(
                f"{self.edge_name} must be in the official range {self.edge_min}..{self.edge_max}; got {size!r}"
            )

    def validate_case(self, case: GraphCase, *, allow_out_of_range=False):
        if not isinstance(case, tuple) or len(case) != 2:
            raise ValueError("Graph case must be (vertex_count, edge_rows)")
        n, edges = case
        if type(n) is not int or not self.vertex_min <= n <= self.vertex_max:
            raise ValueError(f"{self.vertex_name} must be in {self.vertex_min}..{self.vertex_max}")
        if not isinstance(edges, list):
            raise ValueError("Edge rows must be a list")
        m = len(edges)
        self._check_size(m, allow_out_of_range)
        for row in edges:
            if not isinstance(row, tuple) or len(row) != 3 or any(type(x) is not int for x in row):
                raise ValueError("Each edge row must be a tuple of three integers")
            u, v, c = row
            if not (1 <= u <= n and 1 <= v <= n and u != v and 1 <= c <= m):
                raise ValueError(
                    f"Invalid edge {row!r}: endpoints must be distinct in 1..{n}, label in 1..{m}"
                )

    def serialize(self, case: GraphCase, *, allow_out_of_range=False) -> str:
        self.validate_case(case, allow_out_of_range=allow_out_of_range)
        n, edges = case
        return f"{n} {len(edges)}\n" + "".join(f"{u} {v} {c}\n" for u, v, c in edges)

    def strategy(self):
        @st.composite
        def cases(draw):
            n = draw(st.integers(self.vertex_min, min(self.vertex_max, self.stress_max_size)))
            m = draw(st.integers(self.edge_min, self.effective_stress_max_size))

            @st.composite
            def edge(draw):
                u = draw(st.integers(1, n))
                rank = draw(st.integers(1, n - 1))
                v = rank + (rank >= u)
                c = draw(st.integers(1, m))
                return u, v, c

            return n, draw(st.lists(edge(), min_size=m, max_size=m))

        return cases()

    def benchmark_patterns(self, size: int, *, allow_out_of_range=False):
        self._check_size(size, allow_out_of_range)
        return GRAPH_PATTERNS

    def _vertices(self, size, pattern, cap=None):
        maximum = self.vertex_max if cap is None else min(self.vertex_max, cap)
        if pattern == "sparse":
            return maximum
        if pattern == "dense":
            return min(maximum, max(self.vertex_min, math.isqrt(size)))
        if pattern == "parallel":
            return self.vertex_min
        return min(maximum, max(self.vertex_min, size + 1))

    def _case(self, size, seed, pattern, n, color_count=None):
        if pattern not in GRAPH_PATTERNS:
            raise ValueError(f"Unknown graph benchmark pattern: {pattern}")
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        rng = random.Random(seed)
        hub = 2 if n > 2 else 1
        rows = []
        for i in range(size):
            if pattern == "default":
                u = 1 + i % (n - 1)
                v = u + 1
                c = 1 + i % min(2, size)
            elif pattern == "cycle":
                u = 1 + i % n
                v = 1 + u % n
                c = 1 + i % min(2, size)
            elif pattern in {"star_out", "star_in"}:
                other = 2 + i % (n - 1)
                u, v = (1, other) if pattern == "star_out" else (1 + i % (n - 1), n)
                c = i + 1
            elif pattern.startswith("hub_"):
                # Incoming and outgoing roads share a hub, exposing cross-products
                # of incident colors in expanded-state graph algorithms.
                other = 1 + (i // 2) % (n - 1)
                other += other >= hub
                u, v = (other, hub) if i % 2 == 0 else (hub, other)
                c = (
                    i + 1
                    if pattern == "hub_distinct"
                    else 1
                    if pattern == "hub_same"
                    else 1 + i % min(2, size)
                )
            elif pattern == "parallel":
                u, v, c = 1, n, i + 1
            elif pattern == "dense":
                u = 1 + i % n
                rank = 1 + (i // n) % (n - 1)
                v = rank + (rank >= u)
                c = i + 1
            elif pattern == "sparse":
                u, v = 1, 2
                c = 1
            else:
                u = rng.randint(1, n)
                rank = rng.randint(1, n - 1)
                v = rank + (rank >= u)
                c = rng.randint(1, size)
            if color_count is not None:
                c = 1 + (c - 1) % color_count
            rows.append((u, v, c))
        return n, rows

    def benchmark_case(self, size: int, seed: int, *, pattern="default", allow_out_of_range=False):
        self._check_size(size, allow_out_of_range)
        case = self._case(size, seed, pattern, self._vertices(size, pattern))
        self.validate_case(case, allow_out_of_range=allow_out_of_range)
        return case

    def edge_cases(self):
        cases = []
        seen = set()
        for size in sorted(
            {
                self.edge_min,
                min(self.edge_min + 1, self.effective_stress_max_size),
                self.effective_stress_max_size,
            }
        ):
            for pattern in GRAPH_PATTERNS:
                case = self._case(
                    size, 0, pattern, self._vertices(size, pattern, self.stress_max_size)
                )
                self.validate_case(case)
                key = (case[0], tuple(case[1]))
                if key not in seen:
                    seen.add(key)
                    cases.append(case)
        return cases

    def benchmark_strategy(
        self,
        max_size: int,
        seed: int,
        *,
        pattern="default",
        size_only=False,
        allow_out_of_range=False,
    ):
        if pattern not in self.benchmark_patterns(max_size, allow_out_of_range=allow_out_of_range):
            raise ValueError(f"Unknown graph benchmark pattern: {pattern}")
        minimum = min(self.edge_min, max_size) if allow_out_of_range else self.edge_min
        if size_only:
            return st.integers(minimum, max_size).map(
                lambda size: self.benchmark_case(
                    size, seed, pattern=pattern, allow_out_of_range=allow_out_of_range
                )
            )

        @st.composite
        def cases(draw):
            m = draw(st.integers(minimum, max_size))
            original_n = self._vertices(m, pattern)
            n = draw(st.integers(self.vertex_min, original_n))
            colors = draw(st.one_of(st.none(), st.integers(1, m)))
            variant = seed ^ draw(st.integers(0, 65535))
            case = self._case(m, variant, pattern, n, colors)
            self.validate_case(case, allow_out_of_range=allow_out_of_range)
            return case

        return cases()

    def to_toml(self):
        q = json.dumps
        return (
            f"version = 1\n\n[problem]\nurl = {q(self.url)}\n\n"
            f"[vertices]\nname = {q(self.vertex_name)}\nmin = {self.vertex_min}\nmax = {self.vertex_max}\n\n"
            f"[edges]\nname = {q(self.edge_name)}\nmin = {self.edge_min}\nmax = {self.edge_max}\n"
            f"source = {q(self.source_name)}\ntarget = {q(self.target_name)}\nlabel = {q(self.label_name)}\n"
            f"endpoint_min = 1\nendpoint_max = {q(self.vertex_name)}\nlabel_min = 1\nlabel_max = {q(self.edge_name)}\n"
            "allow_self_loops = false\nallow_parallel = true\n\n"
            f"[testing]\nmax_size = {self.stress_max_size}\n"
        )

    @classmethod
    def from_toml(cls, path: Path):
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        if set(data) - {"version", "problem", "vertices", "edges", "testing"}:
            raise ValueError("Unknown constraints keys in graph schema")
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("Constraints version must be the integer 1")

        def table(name, keys, required=()):
            value = data.get(name, {})
            if not isinstance(value, dict) or set(value) - set(keys) or set(required) - set(value):
                raise ValueError(f"Unknown or missing [{name}] settings")
            return value

        problem = table("problem", ("url",))
        vertices = table("vertices", ("name", "min", "max"), ("name", "min", "max"))
        keys = (
            "name",
            "min",
            "max",
            "source",
            "target",
            "label",
            "endpoint_min",
            "endpoint_max",
            "label_min",
            "label_max",
            "allow_self_loops",
            "allow_parallel",
        )
        edges = table("edges", keys, keys)
        testing = table("testing", ("max_size",))
        if not (
            type(edges["endpoint_min"]) is int
            and edges["endpoint_min"] == 1
            and edges["endpoint_max"] == vertices["name"]
            and type(edges["label_min"]) is int
            and edges["label_min"] == 1
            and edges["label_max"] == edges["name"]
            and edges["allow_self_loops"] is False
            and edges["allow_parallel"] is True
        ):
            raise ValueError(
                "Graph schema requires distinct endpoints in 1..N, labels in 1..M, and permits parallel edges"
            )
        return cls(
            vertices["name"],
            vertices["min"],
            vertices["max"],
            edges["name"],
            edges["min"],
            edges["max"],
            edges["source"],
            edges["target"],
            edges["label"],
            testing.get("max_size", 30),
            problem.get("url", ""),
        )
