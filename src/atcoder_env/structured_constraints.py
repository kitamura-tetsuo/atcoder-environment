"""Declarative input generation for composable scalar, array, row and graph shapes.

The recipes are data, not executable statement text. Expressions are limited to
integer arithmetic and previously declared fields. Hypothesis draws small inputs
constructively; large resource searches draw compact size/seed parameters.
"""

from __future__ import annotations

import ast
import json
import math
import random
import string
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from hypothesis import strategies as st

from atcoder_env.constraint_model import BENCHMARK_PATTERNS

MAX_ITEMS = 10_000_000
STRUCTURED_PATTERNS = (*BENCHMARK_PATTERNS, "star_out", "star_in", "cycle", "sparse", "dense")


@lru_cache(maxsize=1024)
def _expression(text: str):
    return ast.parse(text, mode="eval").body


def evaluate(value, env):
    if type(value) is int:
        return value
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("Invalid integer expression")

    def walk(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and type(env.get(node.id)) is int:
            return env[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        if isinstance(node, ast.BinOp):
            a, b = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.FloorDiv):
                return a // b
            if isinstance(node.op, ast.Pow) and 0 <= b <= 4:
                return a**b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [walk(x) for x in node.args]
            if node.func.id == "min":
                return min(args)
            if node.func.id == "max":
                return max(args)
            if node.func.id == "icbrt" and len(args) == 1:
                n = args[0]
                if n < 0:
                    raise ValueError("Negative cube budget")
                x = round(n ** (1 / 3))
                while x**3 > n:
                    x -= 1
                while (x + 1) ** 3 <= n:
                    x += 1
                return x
            if node.func.id == "len" and len(node.args) == 1:
                raise ValueError("Use an explicit length variable")
        raise ValueError(f"Unsupported expression: {value!r}")

    result = walk(_expression(value))
    if abs(result) > 10**40:
        raise ValueError("Integer expression exceeds supported magnitude")
    return result


class Choices:
    def __init__(
        self, seed=0, pattern="random", draw=None, size=None, cap=None, allow_out_of_range=False
    ):
        self.rng = random.Random(seed)
        self.pattern = pattern
        self.draw = draw
        self.size = size
        self.cap = cap
        self.allow_out_of_range = allow_out_of_range

    def integer(self, lo, hi, index=0, count=1):
        if lo > hi:
            raise ValueError(f"Empty integer domain {lo}..{hi}")
        if self.draw is not None:
            return self.draw(st.integers(lo, hi))
        index = min(max(index, 0), max(count - 1, 0))
        p = self.pattern
        if p in {"constant", "default", "sparse"}:
            return lo
        if p in {"constant_max", "dense"}:
            return hi
        if p in {"alternating", "cycle"}:
            return (lo, hi)[index % 2]
        if p == "balanced":
            return lo if index < count // 2 else hi
        if p in {"sorted", "reversed", "palette", "distinct", "blocks"}:
            if p == "reversed":
                index = count - index - 1
            if p == "blocks":
                index = index // max(1, math.isqrt(count)) * max(1, math.isqrt(count))
            return lo + (hi - lo) * index // max(1, count - 1)
        return self.rng.randint(lo, hi)

    def sample(self, width, count):
        if not 0 <= count <= width:
            raise ValueError("Not enough distinct values for the requested input")
        if count > MAX_ITEMS:
            raise ValueError("Generated collection exceeds the local safety cap")
        if self.draw is not None:
            # Sparse Fisher-Yates: no rejection filter and every sample is reachable.
            swaps = {}
            values = []
            for i in range(count):
                j = self.draw(st.integers(i, width - 1))
                values.append(swaps.get(j, j))
                swaps[j] = swaps.get(i, i)
            return values
        if self.pattern in {"constant", "default", "sorted", "sparse"}:
            return list(range(count))
        if self.pattern in {"constant_max", "reversed"}:
            return list(range(width - 1, width - count - 1, -1))
        return self.rng.sample(range(width), count)

    def bounded(self, lo, hi, excluded=(), step=1, index=0, count=1):
        if step < 1 or lo > hi:
            raise ValueError("Invalid integer range")
        width = (hi - lo) // step + 1
        forbidden = sorted(
            {(x - lo) // step for x in excluded if lo <= x <= hi and (x - lo) % step == 0}
        )
        rank = self.integer(0, width - len(forbidden) - 1, index, count)
        for x in forbidden:
            if rank >= x:
                rank += 1
            else:
                break
        return lo + rank * step


def _count(value, env):
    n = evaluate(value, env)
    if not 0 <= n <= MAX_ITEMS:
        raise ValueError(f"Collection size {n} exceeds the local generation limit")
    return n


def _bounds(field, env):
    return evaluate(field["lo"], env), evaluate(field["hi"], env)


def _alphabet(value):
    return string.ascii_lowercase if value == "lowercase" else value


def _word(chooser, alphabet, n, index=0, count=1):
    if chooser.draw is not None:
        return chooser.draw(st.text(alphabet=alphabet, min_size=n, max_size=n))
    return "".join(
        alphabet[chooser.integer(0, len(alphabet) - 1, i + index, max(n, count))] for i in range(n)
    )


def _scalar(field, env, chooser, primary, forced, index=0, count=1):
    name = field.get("name")
    if "literal" in field:
        value = field["literal"]
    elif "alphabet" in field:
        alphabet = _alphabet(field["alphabet"])
        if "length" in field:
            length = _count(field["length"], env)
        else:
            lo, hi = _bounds(field, env)
            if field.get("count") and chooser.cap is not None:
                hi = min(hi, max(lo, chooser.cap))
            if (
                field.get("count")
                and chooser.size is not None
                and chooser.pattern not in {"constant_max", "dense"}
            ):
                hi = min(hi, max(lo, chooser.size))
            length = chooser.integer(lo, hi)
        value = _word(chooser, alphabet, length, index, count)
        if name:
            env[name + "_len"] = length
    else:
        lo, hi = _bounds(field, env)
        if name == primary and forced is not None:
            value = forced
            if (
                value < lo
                or (value > hi and (not chooser.allow_out_of_range or type(field["hi"]) is not int))
                or (value - lo) % field.get("step", 1)
                or value in [evaluate(x, env) for x in field.get("exclude", ())]
            ):
                raise ValueError(f"Benchmark size {value} is outside {name} range {lo}..{hi}")
        else:
            if name == primary or field.get("count"):
                if chooser.cap is not None:
                    hi = min(hi, max(lo, chooser.cap))
                if chooser.size is not None and chooser.pattern not in {"constant_max", "dense"}:
                    hi = min(hi, max(lo, chooser.size))
            excluded = [evaluate(x, env) for x in field.get("exclude", ())]
            step = field.get("step", 1)
            value = chooser.bounded(lo, hi, excluded, step, index, count)
            # Co-scale secondary counts in the standard large-input family.
            if (
                chooser.draw is None
                and chooser.size is not None
                and field.get("count")
                and chooser.pattern == "default"
            ):
                value = lo + (hi - lo) // step * step
    if name:
        env[name] = value
    return str(value)


def _array(op, env, chooser):
    n = _count(op["n"], env)
    lo, hi = _bounds(op, env)
    step = op.get("step", 1)
    excluded = sorted(
        {evaluate(x, env) for x in op.get("exclude", ()) if lo <= evaluate(x, env) <= hi}
    )
    if step != 1 and excluded:
        raise ValueError("Combining stepped and excluded array values is unsupported")
    prefix = [evaluate(x, env) for x in op.get("prefix", ())]
    mode = op.get("mode", "plain")
    if len(prefix) > n:
        raise ValueError("Array prefix exceeds length")
    values = list(prefix)
    if mode in {"unique", "increasing", "permutation"}:
        if step != 1 and prefix:
            raise ValueError("Stepped unique arrays cannot use a prefix")
        forbidden = sorted(set(excluded + prefix))
        width = (hi - lo) // step + 1
        blocked = sorted(
            (x - lo) // step for x in forbidden if lo <= x <= hi and (x - lo) % step == 0
        )
        for rank in chooser.sample(width - len(blocked), n - len(prefix)):
            for x in blocked:
                if rank >= x:
                    rank += 1
                else:
                    break
            values.append(lo + step * rank)
        if mode == "increasing":
            values.sort()
    elif mode == "sum_mod":
        modulus = evaluate(op["mod"], env)
        if n < 1 or modulus < 1:
            raise ValueError("Invalid modular array")
        values.extend(chooser.bounded(lo, hi, excluded, step, i, n) for i in range(n - 1))
        residue = (-sum(values)) % modulus
        start = lo + (residue - lo) % modulus
        values.append(chooser.bounded(start, hi, step=modulus))
    elif mode == "sum_bounded":
        limit = evaluate(op["sum"], env)
        total = chooser.integer(n * lo, min(n * hi, limit))
        for i in range(n):
            remaining = n - i - 1
            v = chooser.integer(
                max(lo, total - remaining * hi), min(hi, total - remaining * lo), i, n
            )
            values.append(v)
            total -= v
    else:
        for i in range(len(prefix), n):
            forbidden = excluded + ([values[-1]] if mode == "adjacent_distinct" and values else [])
            values.append(chooser.bounded(lo, hi, forbidden, step, i, n))
    if len(values) != n or any(
        not lo <= v <= hi or (v - lo) % step or v in excluded for v in values
    ):
        raise ValueError("Array generation violated its domain")
    if op.get("name"):
        env[op["name"] + "_sum"] = sum(values)
    return " ".join(map(str, values))


def _unique_pairs(op, env, chooser):
    n = _count(op["n"], env)
    h, w = evaluate(op["h"], env), evaluate(op["w"], env)
    if op.get("triangle"):
        # Sample ranks in the strict upper triangle without allocating N² pairs.
        width = h * (h - 1) // 2
        for rank in chooser.sample(width, n):
            left, right = 0, h - 1
            while left + 1 < right:
                mid = (left + right) // 2
                if mid * (2 * h - mid - 1) // 2 <= rank:
                    left = mid
                else:
                    right = mid
            offset = rank - left * (2 * h - left - 1) // 2
            yield f"{left + 1} {left + 2 + offset}"
    else:
        for rank in chooser.sample(h * w, n):
            yield f"{rank // w + 1} {rank % w + 1}"


def _tree(op, env, chooser):
    n = _count(op["n"], env)
    # Generate a tree by attaching each vertex to an earlier vertex; shuffle
    # vertex labels in the random family to avoid fixing the topological order.
    labels = chooser.sample(n, n)
    if chooser.pattern == "star_in":
        labels = list(reversed(range(n)))
    edges = []
    for i in range(1, n):
        if chooser.pattern in {"star_out", "star_in", "constant", "constant_max", "dense"}:
            parent = 0
        elif chooser.pattern in {"default", "sorted", "reversed", "cycle", "sparse"}:
            parent = i - 1
        else:
            parent = chooser.integer(0, i - 1)
        u, v = labels[parent] + 1, labels[i] + 1
        if op.get("ordered"):
            u, v = sorted((u, v))
        edges.append((u, v))
    for u, v in edges:
        yield f"{u} {v}"


def _reachable(op, env, chooser):
    n, m = _count(op["vertices"], env), _count(op["n"], env)
    if m < n - 1:
        raise ValueError("Reachable graph needs at least N-1 edges")
    labels = [1] + [x + 2 for x in chooser.sample(n - 1, n - 1)]
    for i in range(1, n):
        parent = (
            0
            if chooser.pattern in {"star_out", "dense", "constant"}
            else i - 1
            if chooser.pattern in {"default", "sparse", "sorted"}
            else chooser.integer(0, i - 1)
        )
        yield f"{labels[parent]} {labels[i]} {chooser.integer(1, 9, i, m)}"
    for i in range(m - n + 1):
        u = chooser.integer(1, n, i, m)
        v = chooser.integer(1, n, i + 1, m)
        yield f"{u} {v} {chooser.integer(1, 9, i, m)}"


def _string_rows(op, env, chooser):
    n = _count(op["n"], env)
    alphabet = _alphabet(op["alphabet"])
    if "length" in op:
        lo = hi = _count(op["length"], env)
    else:
        lo, hi = _bounds(op, env)
    if op.get("unique"):
        length = lo
        while len(alphabet) ** length < n and length < hi:
            length += 1
        length = chooser.integer(length, hi)
        for rank in chooser.sample(len(alphabet) ** length, n):
            chars = []
            for _ in range(length):
                rank, digit = divmod(rank, len(alphabet))
                chars.append(alphabet[digit])
            yield "".join(reversed(chars))
    else:
        for i in range(n):
            length = chooser.integer(lo, hi, i, n)
            yield _word(chooser, alphabet, length, i, n)


def _steps(steps, env, chooser, primary, forced, lines):
    for op in steps:
        kind = op["op"]
        if kind in {"line", "set"}:
            tokens = [_scalar(f, env, chooser, primary, forced) for f in op["fields"]]
            if kind == "line":
                lines.append(op.get("separator", " ").join(tokens))
        elif kind == "array":
            lines.append(_array(op, env, chooser))
        elif kind == "rows":
            n = _count(op["n"], env)
            for i in range(n):
                scope = env.copy()
                variants = op["variants"]
                fields = variants[chooser.integer(0, len(variants) - 1, i, n)]
                lines.append(" ".join(_scalar(f, scope, chooser, None, None, i, n) for f in fields))
        elif kind == "unique_pairs":
            lines.extend(_unique_pairs(op, env, chooser))
        elif kind == "tree":
            lines.extend(_tree(op, env, chooser))
        elif kind == "reachable":
            lines.extend(_reachable(op, env, chooser))
        elif kind == "string_rows":
            lines.extend(_string_rows(op, env, chooser))
        elif kind == "clogged":
            n, m = _count(op["n"], env), _count(op["m"], env)
            selected = chooser.integer(0, n + m - 1)
            lines.extend(
                "".join("x" if i == selected or n + j == selected else "o" for j in range(m))
                for i in range(n)
            )
        elif kind == "permutation_grid":
            alphabet = op["alphabet"]
            values = [alphabet[i] for i in chooser.sample(len(alphabet), len(alphabet))]
            width = op["width"]
            lines.extend("".join(values[i : i + width]) for i in range(0, len(values), width))
        elif kind == "points":
            n = _count(op["n"], env)
            # Distinct slopes from the origin, valid also when coordinates are
            # negative. Construct rather than rejecting potentially N² pairs.
            ranks = chooser.sample(2_000_000_001, n)
            sign = -1 if chooser.integer(0, 1) else 1
            flip = chooser.integer(0, 1)
            for rank in ranks:
                x = rank - 1_000_000_000
                a, b = (sign, x) if flip else (x, sign)
                lines.append(f"{a} {b}")
        elif kind == "cases":
            count = _count(op["n"], env)
            budgets = op.get("budgets", {})
            shared = {name: evaluate(value, env) for name, value in budgets.items()}
            for i in range(count):
                scope = env | shared | {"remaining_cases": count - i - 1}
                _steps(op["steps"], scope, chooser, None, None, lines)
                for name, expr in op.get("subtract", {}).items():
                    shared[name] -= evaluate(expr, scope)
                    if shared[name] < 0:
                        raise ValueError("Aggregate input budget exhausted")
        else:
            raise ValueError(f"Unknown structured input operation: {kind}")


@dataclass(frozen=True)
class StructuredCase:
    text: str


@dataclass(frozen=True)
class StructuredConstraints:
    size_name: str
    size_min: int
    size_max: int
    steps: list[dict]
    stress_max_size: int = 30
    url: str = ""
    primary_step: int = 1

    def __post_init__(self):
        if not isinstance(self.size_name, str) or not self.size_name.isidentifier():
            raise ValueError("Invalid primary size name")
        if any(type(v) is not int for v in (self.size_min, self.size_max, self.stress_max_size)):
            raise ValueError("Size bounds must be integers")
        if (
            not 1 <= self.size_min <= self.size_max
            or self.stress_max_size < 1
            or type(self.primary_step) is not int
            or self.primary_step < 1
        ):
            raise ValueError("Invalid structured size range")
        if not isinstance(self.steps, list) or not self.steps:
            raise ValueError("Structured input steps must be a nonempty list")

    @property
    def effective_stress_max_size(self):
        limit = min(self.size_max, max(self.size_min, self.stress_max_size))
        return self.size_min + (limit - self.size_min) // self.primary_step * self.primary_step

    def _make(self, chooser, forced=None):
        env = {"size": forced or 1, self.size_name: forced or 1}
        lines = []
        _steps(self.steps, env, chooser, self.size_name, forced, lines)
        if self.size_name != "size" and self.size_name not in env:
            raise ValueError(f"Primary size {self.size_name} not generated")
        return StructuredCase("\n".join(lines) + "\n")

    def strategy(self):
        @st.composite
        def cases(draw):
            size = self.size_min + self.primary_step * draw(
                st.integers(
                    0, (self.effective_stress_max_size - self.size_min) // self.primary_step
                )
            )
            return self._make(Choices(draw=draw, cap=self.stress_max_size), size)

        return cases()

    def serialize(self, case, *, allow_out_of_range=False):
        if not isinstance(case, StructuredCase):
            raise ValueError("Expected a generated StructuredCase")
        return case.text

    def benchmark_patterns(self, size, *, allow_out_of_range=False):
        if (
            type(size) is not int
            or size < 1
            or size < self.size_min
            or (size > self.size_max and (not allow_out_of_range or self.size_name == "size"))
            or (size - self.size_min) % self.primary_step
        ):
            raise ValueError(
                f"Invalid structured benchmark size for {self.size_name}: {size}; official bounds {self.size_min}..{self.size_max}, step {self.primary_step}"
            )
        return STRUCTURED_PATTERNS if self._has_graph(self.steps) else BENCHMARK_PATTERNS

    @staticmethod
    def _has_graph(steps):
        return any(
            op["op"] in {"tree", "reachable"}
            or (op["op"] == "cases" and StructuredConstraints._has_graph(op["steps"]))
            for op in steps
        )

    def benchmark_case(self, size, seed, *, pattern="default", allow_out_of_range=False):
        if pattern not in self.benchmark_patterns(size, allow_out_of_range=allow_out_of_range):
            raise ValueError(f"Unknown structured pattern {pattern}")
        return self._make(
            Choices(seed, pattern, size=size, allow_out_of_range=allow_out_of_range), size
        )

    def benchmark_strategy(
        self, max_size, seed, *, pattern="default", size_only=False, allow_out_of_range=False
    ):
        self.benchmark_patterns(max_size, allow_out_of_range=allow_out_of_range)

        @st.composite
        def cases(draw):
            n = self.size_min + self.primary_step * draw(
                st.integers(0, (max_size - self.size_min) // self.primary_step)
            )
            variant = seed if size_only else seed ^ draw(st.integers(0, 65535))
            return self.benchmark_case(
                n, variant, pattern=pattern, allow_out_of_range=allow_out_of_range
            )

        return cases()

    def edge_cases(self):
        sizes = {
            self.size_min,
            min(self.size_min + self.primary_step, self.effective_stress_max_size),
            self.effective_stress_max_size,
        }
        return list(
            dict.fromkeys(
                self._make(Choices(0, p, size=n, cap=self.stress_max_size), n)
                for n in sorted(sizes)
                for p in ("default", "constant", "constant_max", "alternating", "random")
            )
        )

    def default_benchmark_sizes(self):
        sizes = {
            self.size_min,
            self.size_min
            + (self.size_max - self.size_min) // self.primary_step * self.primary_step,
        }
        power = 10
        while power < self.size_max:
            if power > self.size_min:
                sizes.add(
                    self.size_min + (power - self.size_min) // self.primary_step * self.primary_step
                )
            power *= 10
        return sorted(sizes)

    def to_toml(self):
        return (
            "version = 1\n\n[problem]\nurl = "
            + json.dumps(self.url)
            + "\n\n[size]\nname = "
            + json.dumps(self.size_name)
            + f"\nmin = {self.size_min}\nmax = {self.size_max}\nstep = {self.primary_step}\n\n[structured]\n"
            + "steps = "
            + json.dumps(json.dumps(self.steps, ensure_ascii=True, separators=(",", ":")))
            + f"\n\n[testing]\nmax_size = {self.stress_max_size}\n"
        )

    @classmethod
    def from_toml(cls, path: Path):
        with path.open("rb") as f:
            data = tomllib.load(f)
        if (
            set(data) != {"version", "problem", "size", "structured", "testing"}
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise ValueError("Invalid structured constraints schema")
        if (
            set(data["size"]) != {"name", "min", "max", "step"}
            or set(data["structured"]) != {"steps"}
            or set(data["testing"]) != {"max_size"}
            or set(data["problem"]) != {"url"}
        ):
            raise ValueError("Unknown or missing structured constraints settings")
        size = data["size"]
        return cls(
            size["name"],
            size["min"],
            size["max"],
            json.loads(data["structured"]["steps"]),
            data["testing"]["max_size"],
            data["problem"]["url"],
            size["step"],
        )
