"""Adapt existing input/LaTeX parsers to a deliberately narrow constraint model.

Every clause in the English Constraints section must be accounted for. Unsupported
math, prose, or input shapes produce diagnostics rather than a partial generator.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache

import sympy
from bs4 import BeautifulSoup, Tag
from onlinejudge_template.analyzer import html as oj_html
from onlinejudge_template.analyzer import parser as oj_parser
from onlinejudge_template.analyzer import variables as oj_variables
from onlinejudge_template.types import ItemNode, LoopNode, NewlineNode, SequenceNode
from sympy.parsing.latex import parse_latex

from atcoder_env.constraint_model import IntegerArrayConstraints
from atcoder_env.graph_constraints import GraphConstraints
from atcoder_env.string_constraints import StringConstraints

MAX_VALUE = 10**18
MAX_EXPRESSION_LENGTH = 256
MAX_EXPRESSION_DEPTH = 16
MAX_EXPRESSION_NODES = 64
MAX_HTML_BYTES = 4 * 1024 * 1024
_ARITHMETIC_COMMANDS = {"times", "cdot", "frac", "dfrac", "left", "right"}
_INTEGER_CLAUSE = re.compile(
    r"(?:All (?:the )?input values|All values in the input|All inputs) are integers\.?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConstraintAnalysis:
    spec: IntegerArrayConstraints | StringConstraints | GraphConstraints | None
    input_format: str | None
    constraints: list[str]
    recognized: list[str]
    unsupported: list[str]


class UnsupportedConstraint(ValueError):
    """The statement cannot be represented without dropping a condition."""


def _normalize(text: str) -> str:
    text = text.replace("−", "-").replace("×", r"\times")
    text = text.replace("≤", "<=").replace("≥", ">=")
    text = re.sub(r"\\(?:leq|le)\b", "<=", text)
    text = re.sub(r"\\(?:geq|ge)\b", ">=", text)
    text = re.sub(r"\\lt\b", "<", text)
    text = re.sub(r"\\gt\b", ">", text)
    text = re.sub(r"\\(?:quad|qquad)\b|\\[,!;: ]", " ", text)
    text = text.replace("~", " ")
    return " ".join(text.split())


def _math_preflight(text: str) -> None:
    if not text or len(text) > MAX_EXPRESSION_LENGTH:
        raise UnsupportedConstraint("Math expression is empty or exceeds the length limit")
    if re.search(r"[^A-Za-z0-9_{}()+*/^.\\\s-]", text):
        raise UnsupportedConstraint("Unsupported character in a math expression")
    commands = re.findall(r"\\([A-Za-z]+)", text)
    if any(command not in _ARITHMETIC_COMMANDS for command in commands):
        raise UnsupportedConstraint("Unsupported LaTeX command in a bound")
    if any(len(number) > 19 for number in re.findall(r"\d+", text)):
        raise UnsupportedConstraint("Integer literal exceeds the numeric limit")
    stack = []
    for character in text:
        if character in "({":
            stack.append(character)
            if len(stack) > MAX_EXPRESSION_DEPTH:
                raise UnsupportedConstraint("Math expression nesting exceeds the limit")
        elif character in ")}":
            if not stack or stack.pop() != {"}": "{", ")": "("}[character]:
                raise UnsupportedConstraint("Unbalanced math expression")
    if stack:
        raise UnsupportedConstraint("Unbalanced math expression")


def _literal_exponent(expression: sympy.Basic) -> int:
    if isinstance(expression, sympy.Integer):
        value = int(expression)
    elif (
        isinstance(expression, sympy.Mul)
        and len(expression.args) == 2
        and expression.args[0] == -1
        and isinstance(expression.args[1], sympy.Integer)
    ):
        value = -int(expression.args[1])
    else:
        raise UnsupportedConstraint("Exponents must be literal signed integers")
    if abs(value) > 64:
        raise UnsupportedConstraint("Exponent exceeds the supported limit")
    return value


def _validate_tree(expression: sympy.Basic) -> None:
    pending = [(expression, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_EXPRESSION_NODES or depth > MAX_EXPRESSION_DEPTH:
            raise UnsupportedConstraint("Math expression complexity exceeds the limit")
        if not isinstance(
            node, (sympy.Integer, sympy.Rational, sympy.Symbol, sympy.Add, sympy.Mul, sympy.Pow)
        ):
            raise UnsupportedConstraint(
                "Only integer arithmetic and known variable names are supported"
            )
        if isinstance(node, sympy.Integer) and abs(int(node)) > MAX_VALUE:
            raise UnsupportedConstraint("Numeric value exceeds 10^18")
        if isinstance(node, sympy.Pow):
            _literal_exponent(node.args[1])
        pending.extend((argument, depth + 1) for argument in node.args)


@lru_cache(maxsize=256)
def _parse_math(text: str) -> sympy.Basic:
    _math_preflight(text)
    try:
        # The strict ANTLR parser consumes the whole input and retains unevaluated
        # Add/Mul/Pow nodes. Never pass statement strings to sympify or parse_expr.
        expression = parse_latex(text, backend="antlr", strict=True)
    except Exception as error:
        raise UnsupportedConstraint(f"Cannot parse math expression {text!r}") from error
    _validate_tree(expression)
    return expression


def _bounded(value: Fraction) -> Fraction:
    if abs(value.numerator) > MAX_VALUE or value.denominator > MAX_VALUE:
        raise UnsupportedConstraint("Numeric value or intermediate fraction exceeds 10^18")
    return value


def _numeric(expression: sympy.Basic) -> Fraction:
    if isinstance(expression, sympy.Integer):
        return _bounded(Fraction(int(expression)))
    if isinstance(expression, sympy.Rational):
        return _bounded(Fraction(int(expression.p), int(expression.q)))
    if isinstance(expression, sympy.Add):
        result = Fraction(0)
        for term in expression.args:
            result = _bounded(result + _numeric(term))
        return result
    if isinstance(expression, sympy.Mul):
        result = Fraction(1)
        for term in expression.args:
            result = _bounded(result * _numeric(term))
        return result
    if isinstance(expression, sympy.Pow):
        base = _numeric(expression.args[0])
        exponent = _literal_exponent(expression.args[1])
        if base == 0 and exponent < 0:
            raise UnsupportedConstraint("A bound cannot divide by zero")
        return _bounded(base**exponent)
    raise UnsupportedConstraint("Symbolic arithmetic or an unknown variable appears in a bound")


def _endpoint(
    expression: sympy.Basic, *, lower: bool, strict: bool, size_name: str | None = None
) -> int | str:
    if isinstance(expression, sympy.Symbol) and str(expression) == size_name:
        if strict:
            raise UnsupportedConstraint(
                "Strict bounds depending on the size require an unsupported offset"
            )
        return size_name
    value = _numeric(expression)
    if lower:
        result = math.floor(value) + 1 if strict else math.ceil(value)
    else:
        result = math.ceil(value) - 1 if strict else math.floor(value)
    if abs(result) > MAX_VALUE:
        raise UnsupportedConstraint("Integer endpoint exceeds 10^18")
    return result


def _input_shape(input_format: str) -> tuple[str, ...]:
    try:
        tree = oj_parser.run(input_format)
        declared = oj_variables.list_declared_variables(tree)
    except Exception as error:
        raise UnsupportedConstraint(
            "Existing oj-template parser could not parse the input format"
        ) from error
    # Reuse the oj-template AST for the repeated-row shape.
    if isinstance(tree, SequenceNode) and len(tree.items) == 4:
        vertex, edge, newline, loop = tree.items
        if (
            isinstance(vertex, ItemNode)
            and not vertex.indices
            and isinstance(edge, ItemNode)
            and not edge.indices
            and isinstance(newline, NewlineNode)
            and isinstance(loop, LoopNode)
            and str(loop.size) == str(edge.name)
            and isinstance(loop.body, SequenceNode)
            and len(loop.body.items) == 4
            and isinstance(loop.body.items[-1], NewlineNode)
        ):
            fields = loop.body.items[:3]
            if all(isinstance(item, ItemNode) and len(item.indices) == 1 for item in fields):
                vertex_name, edge_name = str(vertex.name), str(edge.name)
                names = [str(item.name) for item in fields]
                if (
                    len({vertex_name, edge_name, *names}) == 5
                    and set(declared) == {vertex_name, edge_name, *names}
                    and all(
                        list(map(str, declared[name].dims)) == [edge_name]
                        and list(map(str, declared[name].bases)) == ["1"]
                        and set(declared[name].depending) == {edge_name}
                        for name in names
                    )
                    and all(str(item.indices[0]) == f"{loop.name} + 1" for item in fields)
                ):
                    return vertex_name, edge_name, "graph", *names
    shape_error = (
        "Supported input formats are: size + one array/string line, or "
        "two size variables followed by a repeated three-integer edge row"
    )
    if not isinstance(tree, SequenceNode) or len(tree.items) != 4:
        raise UnsupportedConstraint(shape_error)
    scalar, first_newline, payload, last_newline = tree.items
    if not (
        isinstance(scalar, ItemNode)
        and not scalar.indices
        and isinstance(first_newline, NewlineNode)
        and isinstance(last_newline, NewlineNode)
    ):
        raise UnsupportedConstraint(shape_error)
    if isinstance(payload, ItemNode) and not payload.indices:
        size_name, string_name = str(scalar.name), str(payload.name)
        if size_name == string_name or set(declared) != {size_name, string_name}:
            raise UnsupportedConstraint("Input must declare one size and one separate string")
        return size_name, string_name, "string"
    loop = payload
    if not (
        isinstance(loop, LoopNode)
        and isinstance(loop.body, ItemNode)
        and len(loop.body.indices) == 1
    ):
        raise UnsupportedConstraint(shape_error)
    size_name, values_name = str(scalar.name), str(loop.body.name)
    if size_name == values_name or set(declared) != {size_name, values_name}:
        raise UnsupportedConstraint("Input must declare one size and one separate array")
    array = declared[values_name]
    if (
        str(loop.size) != size_name
        or list(map(str, array.dims)) != [size_name]
        or list(map(str, array.bases)) != ["1"]
        or set(array.depending) != {size_name}
    ):
        raise UnsupportedConstraint("Only an exactly size-long, one-based array is supported")
    return size_name, values_name, "array"


def _clauses(section: Tag) -> list[str]:
    fragment = BeautifulSoup(str(section), "html.parser")
    heading = fragment.find("h3")
    if heading is not None:
        heading.decompose()
    entries = [node for node in fragment.find_all(("li", "p")) if node.find_parent("li") is None]
    result = []
    for entry in entries:
        text = " ".join(entry.get_text(" ", strip=True).split())
        if text:
            result.append(text)
        entry.decompose()
    remainder = " ".join(fragment.get_text(" ", strip=True).split())
    if remainder:
        result.append(remainder)
    return result


def _array_index(expression: sympy.Basic, values_name: str, size_name: str) -> str | None:
    if isinstance(expression, sympy.Symbol):
        match = re.fullmatch(re.escape(values_name) + r"_\{([A-Za-z])\}", str(expression))
        if match and match[1] not in {size_name, values_name}:
            return match[1]
    return None


def _comparisons(text: str) -> list[tuple[sympy.Basic, str, sympy.Basic]]:
    pieces = re.split(r"(<=|>=|<|>)", text)
    if len(pieces) < 3 or len(pieces) > 9:
        raise UnsupportedConstraint("Expected a supported inequality involving an input variable")
    operands = [_parse_math(piece.strip()) for piece in pieces[::2]]
    result = []
    for index, operator in enumerate(pieces[1::2]):
        left, right = operands[index], operands[index + 1]
        if operator in {">", ">="}:
            left, right = right, left
            operator = "<" if operator == ">" else "<="
        result.append((left, operator, right))
    return result


def _trailing_domain(text: str) -> tuple[str, str | None]:
    text = re.sub(r"\\(?:left|right)\b", "", text)
    if text.endswith(")"):
        level = 0
        for offset in range(len(text) - 1, -1, -1):
            if text[offset] == ")":
                level += 1
            elif text[offset] == "(":
                level -= 1
                if level == 0:
                    domain = text[offset + 1 : -1]
                    if offset and re.search(r"[<>]", domain):
                        return text[:offset].strip(), domain
                    break
    return text, None


def _verify_index_domain(domain: str, indices: set[str], size_name: str) -> None:
    if len(indices) != 1:
        raise UnsupportedConstraint("Index-domain annotation does not identify this array's index")
    index_name = next(iter(indices))
    bounds: dict[str, int | str] = {}
    relations = _comparisons(domain)
    if len(relations) != 2:
        raise UnsupportedConstraint("Only the complete index domain 1 <= i <= size is supported")
    for left, operator, right in relations:
        if isinstance(right, sympy.Symbol) and str(right) == index_name:
            side, bound = "lower", left
        elif isinstance(left, sympy.Symbol) and str(left) == index_name:
            side, bound = "upper", right
        else:
            raise UnsupportedConstraint("Index-domain annotation refers to another variable")
        if side in bounds:
            raise UnsupportedConstraint("Index-domain annotation repeats a bound")
        bounds[side] = _endpoint(
            bound, lower=side == "lower", strict=operator == "<", size_name=size_name
        )
    if bounds != {"lower": 1, "upper": size_name}:
        raise UnsupportedConstraint(
            "Index-domain annotation does not cover the complete input array"
        )


def _bound_clause(text: str, size_name: str, values_name: str) -> list[tuple[str, str, int | str]]:
    normalized = _normalize(text).removesuffix(".").strip()
    body, domain = _trailing_domain(normalized)
    updates = []
    indices = set()
    for left, operator, right in _comparisons(body):
        left_index, right_index = (
            _array_index(left, values_name, size_name),
            _array_index(right, values_name, size_name),
        )
        if left_index is not None and right_index is None:
            target, side, bound = "values", "upper", right
            indices.add(left_index)
        elif right_index is not None and left_index is None:
            target, side, bound = "values", "lower", left
            indices.add(right_index)
        elif left_index is not None or right_index is not None:
            raise UnsupportedConstraint(
                "Relations between array elements require a different model"
            )
        elif isinstance(left, sympy.Symbol) and str(left) == size_name:
            target, side, bound = "size", "upper", right
        elif isinstance(right, sympy.Symbol) and str(right) == size_name:
            target, side, bound = "size", "lower", left
        else:
            raise UnsupportedConstraint(
                "Inequality does not bound the declared size or array elements"
            )
        endpoint = _endpoint(
            bound,
            lower=side == "lower",
            strict=operator == "<",
            size_name=size_name if target == "values" else None,
        )
        updates.append((target, side, endpoint))
    if len(indices) > 1:
        raise UnsupportedConstraint(
            "A constraint uses different array indices without a supported quantifier"
        )
    if domain is not None:
        _verify_index_domain(domain, indices, size_name)
    return updates


def _distinct_clause(text: str, size_name: str, values_name: str) -> bool:
    match = re.fullmatch(
        r"At least (?:two|2) distinct values appear in (.+?)\s*\.?", text, re.IGNORECASE
    )
    if match is None:
        return False
    sequence = match[1].strip().removesuffix(".").strip()
    sequence = re.sub(r"\\(?:ldots|cdots|dots)\b", "...", sequence)
    sequence = re.sub(r"_\{([A-Za-z0-9]+)\}", r"_\1", sequence)
    sequence = re.sub(r"\s+", "", sequence)
    expected = f"{values_name}_1,{values_name}_2,...,{values_name}_{size_name}"
    if sequence != expected:
        raise UnsupportedConstraint("Distinctness clause does not name the complete declared array")
    return True


def _combined_bound(
    bounds: list[int | str], *, lower: bool, size_name: str, size_min: int, size_max: int
) -> int | str:
    constants = [bound for bound in bounds if isinstance(bound, int)]
    constant = (max(constants) if lower else min(constants)) if constants else None
    if size_name not in bounds:
        assert constant is not None
        return constant
    if constant is None:
        return size_name
    if lower:
        if constant <= size_min:
            return size_name
        if constant >= size_max:
            return constant
    else:
        if constant >= size_max:
            return size_name
        if constant <= size_min:
            return constant
    raise UnsupportedConstraint(
        "Combined constant and size-dependent bounds need an unsupported min/max expression"
    )


def _literal_alphabet(text: str) -> str:
    """Consume an explicit enumeration of single printable ASCII characters."""
    atom = re.compile(r"""`([^\s`])`|'([^\s'])'|"([^\s"])"|([^\s,.'"`])""")
    characters: list[str] = []
    remaining = text.strip()
    final_character = False
    while True:
        match = atom.match(remaining)
        if match is None:
            raise UnsupportedConstraint("String alphabet must list individual literal characters")
        character = next(group for group in match.groups() if group is not None)
        if not character.isascii() or not character.isprintable():
            raise UnsupportedConstraint("Only printable ASCII string alphabets are supported")
        characters.append(character)
        remaining = remaining[match.end() :].lstrip()
        if not remaining:
            break
        if final_character:
            raise UnsupportedConstraint("Unexpected text after the final alphabet character")
        separator = re.match(r",\s*(?:(and)\s+)?|(and)\s+", remaining, re.IGNORECASE)
        if separator is None:
            raise UnsupportedConstraint("String alphabet must be a complete literal enumeration")
        final_character = any(group is not None for group in separator.groups())
        remaining = remaining[separator.end() :]
    if len(set(characters)) != len(characters):
        raise UnsupportedConstraint("String alphabet contains duplicate characters")
    return "".join(characters)


def _string_clause(text: str, size_name: str, string_name: str) -> str | None:
    match = re.fullmatch(
        r"([A-Za-z][A-Za-z0-9_]*) (?i:is a string of length) (.+?) "
        r"(?i:consisting of) (.+?)\s*\.?",
        " ".join(text.split()),
    )
    if match is None:
        return None
    if match[1] != string_name:
        raise UnsupportedConstraint("String clause does not name the declared input string")
    if match[2].strip() != size_name:
        raise UnsupportedConstraint("String length must be exactly the declared size variable")
    return _literal_alphabet(match[3])


def _string_spec(
    constraints: list[str],
    recognized: list[str],
    unsupported: list[str],
    *,
    size_name: str,
    string_name: str,
    max_size: int,
    url: str,
) -> StringConstraints | None:
    bounds: dict[str, list[int]] = {"lower": [], "upper": []}
    integer_size = False
    alphabet = None
    for clause in constraints:
        try:
            if re.fullmatch(
                re.escape(size_name) + r" (?i:is an integer)\s*\.?", _normalize(clause)
            ):
                integer_size = True
            else:
                declared_alphabet = _string_clause(clause, size_name, string_name)
                if declared_alphabet is not None:
                    if alphabet is not None and set(alphabet) != set(declared_alphabet):
                        raise UnsupportedConstraint("Conflicting string alphabet declarations")
                    alphabet = declared_alphabet
                else:
                    updates = _bound_clause(clause, size_name, string_name)
                    if any(target != "size" for target, _, _ in updates):
                        raise UnsupportedConstraint(
                            "String input format cannot use integer-array element constraints"
                        )
                    for _, side, endpoint in updates:
                        assert isinstance(endpoint, int)
                        bounds[side].append(endpoint)
            recognized.append(clause)
        except UnsupportedConstraint as error:
            unsupported.append(f"{clause}: {error}")
    if not integer_size:
        unsupported.append(f"Missing an explicit clause declaring {size_name} to be an integer")
    if alphabet is None:
        unsupported.append("Missing an explicit string length and literal alphabet declaration")
    for side, entries in bounds.items():
        if not entries:
            unsupported.append(f"Missing {side} bound for size")
    if unsupported:
        return None
    try:
        return StringConstraints(
            size_name=size_name,
            size_min=max(bounds["lower"]),
            size_max=min(bounds["upper"]),
            string_name=string_name,
            alphabet=alphabet,
            stress_max_size=max_size,
            url=url,
        )
    except ValueError as error:
        unsupported.append(f"Constraints cannot be represented by the supported model: {error}")
        return None


def _graph_spec(
    constraints: list[str],
    recognized: list[str],
    unsupported: list[str],
    *,
    vertex_name: str,
    edge_name: str,
    fields: list[str],
    max_size: int,
    url: str,
) -> GraphConstraints | None:
    """Recognize the complete labeled-edge schema, without dropping unknown clauses."""
    source, target, label = fields
    dependencies = {source: vertex_name, target: vertex_name, label: edge_name}
    names = [vertex_name, edge_name, *fields]
    bounds: dict[tuple[str, str], list[int | str]] = {
        (name, side): [] for name in names for side in ("lower", "upper")
    }
    integers = False
    no_self_loops = False

    def field_updates(body: str, domain: str | None, name: str):
        dependency = dependencies[name]
        updates = _bound_clause(body, dependency, name)
        if not updates or any(kind != "values" for kind, _, _ in updates):
            raise UnsupportedConstraint("Expected bounds on a repeated edge field")
        indices = {
            index
            for left, _, right in _comparisons(body)
            for operand in (left, right)
            if (index := _array_index(operand, name, dependency)) is not None
        }
        if len(indices) != 1 or indices.intersection(names):
            raise UnsupportedConstraint("Edge field bounds must use one independent row index")
        if domain is not None:
            _verify_index_domain(domain, indices, edge_name)
        return [(name, side, value) for _, side, value in updates]

    def parse_bounds(body: str, domain: str | None):
        parts = re.split(r"(<=|>=|<|>)", body)
        if len(parts) == 5 and "," in parts[2]:
            # A grouped middle operand is an explicit conjunction of bounds.
            # Only the two declared endpoints with the same row index qualify.
            grouped = [item.strip() for item in parts[2].split(",")]
            if len(grouped) != 2:
                raise UnsupportedConstraint("Expected exactly two grouped endpoint variables")
            left, right = map(_parse_math, grouped)
            a = _array_index(left, source, vertex_name)
            b = _array_index(right, target, vertex_name)
            if a is None or b is None:
                a = _array_index(right, source, vertex_name)
                b = _array_index(left, target, vertex_name)
                grouped.reverse()
            if a is None or a != b or a in names:
                raise UnsupportedConstraint("Grouped endpoint bounds must use the same row index")
            updates = []
            for name, atom in zip((source, target), grouped, strict=True):
                updates.extend(field_updates("".join([*parts[:2], atom, *parts[3:]]), domain, name))
            return updates
        for name in fields:
            try:
                return field_updates(body, domain, name)
            except UnsupportedConstraint:
                pass
        if domain is None:
            for name in (vertex_name, edge_name):
                try:
                    updates = _bound_clause(body, name, "_unused")
                    if updates and all(kind == "size" for kind, _, _ in updates):
                        return [(name, side, value) for _, side, value in updates]
                except UnsupportedConstraint:
                    pass
        raise UnsupportedConstraint("Expected a scalar bound or a bound on a declared edge field")

    for clause in constraints:
        try:
            body, domain = _trailing_domain(_normalize(clause).removesuffix(".").strip())
            if _INTEGER_CLAUSE.fullmatch(clause):
                integers = True
            elif re.search(r"\\(?:neq|ne)\b|!=|≠", body):
                parts = re.split(r"\\(?:neq|ne)\b|!=|≠", body)
                if len(parts) != 2:
                    raise UnsupportedConstraint("Expected one endpoint inequality")
                left, right = (_parse_math(part.strip()) for part in parts)
                a, b = (
                    _array_index(left, source, vertex_name),
                    _array_index(right, target, vertex_name),
                )
                if a is None or b is None:
                    a, b = (
                        _array_index(right, source, vertex_name),
                        _array_index(left, target, vertex_name),
                    )
                if a is None or a != b or a in names:
                    raise UnsupportedConstraint(
                        "Self-loop prohibition must compare the two endpoints at the same row index"
                    )
                if domain is not None:
                    _verify_index_domain(domain, {a}, edge_name)
                no_self_loops = True
            else:
                for name, side, value in parse_bounds(body, domain):
                    bounds[name, side].append(value)
            recognized.append(clause)
        except UnsupportedConstraint as error:
            unsupported.append(f"{clause}: {error}")
    if not integers:
        unsupported.append("Missing an explicit clause declaring all input values to be integers")
    if not no_self_loops:
        unsupported.append(f"Missing explicit {source}_i != {target}_i (self-loop prohibition)")
    for (name, side), entries in bounds.items():
        if not entries:
            unsupported.append(f"Missing {side} bound for {name}")
    if unsupported:
        return None
    try:
        ranges = {
            name: (max(bounds[name, "lower"]), min(bounds[name, "upper"]))
            for name in (vertex_name, edge_name)
        }
        for name, dependency in dependencies.items():
            low, high = ranges[dependency]
            lower = _combined_bound(
                bounds[name, "lower"], lower=True, size_name=dependency, size_min=low, size_max=high
            )
            upper = _combined_bound(
                bounds[name, "upper"],
                lower=False,
                size_name=dependency,
                size_min=low,
                size_max=high,
            )
            if (lower, upper) != (1, dependency):
                raise UnsupportedConstraint(
                    f"{name} must have the supported complete range 1..{dependency}"
                )
        return GraphConstraints(
            vertex_name,
            *ranges[vertex_name],
            edge_name,
            *ranges[edge_name],
            source,
            target,
            label,
            max_size,
            url,
        )
    except ValueError as error:
        unsupported.append(
            f"Constraints cannot be represented by the supported graph model: {error}"
        )
        return None


def analyze_statement(html: bytes, url: str, *, max_size: int = 30) -> ConstraintAnalysis:
    """Extract a complete supported model, preserving every unresolved condition."""
    constraints: list[str] = []
    recognized: list[str] = []
    unsupported: list[str] = []
    input_format = None

    def result(
        spec: IntegerArrayConstraints | StringConstraints | GraphConstraints | None = None,
    ) -> ConstraintAnalysis:
        return ConstraintAnalysis(spec, input_format, constraints, recognized, unsupported)

    if type(max_size) is not int or not 1 <= max_size <= MAX_VALUE:
        unsupported.append("Stress max_size must be a positive integer no greater than 10^18")
        return result()
    if len(html) > MAX_HTML_BYTES:
        unsupported.append("Statement HTML exceeds the 4 MiB limit")
        return result()
    soup = BeautifulSoup(html, "html.parser")
    english_sections = soup.select(".lang-en")
    if len(english_sections) != 1:
        unsupported.append("A single English .lang-en statement section is required")
        return result()
    english = english_sections[0]
    headings = [
        heading
        for heading in english.find_all("h3")
        if heading.get_text(" ", strip=True) == "Constraints"
    ]
    if len(headings) != 1:
        unsupported.append("A single English Constraints section is required")
        return result()
    section = headings[0].find_parent("section") or headings[0].parent
    constraints.extend(_clauses(section))
    if not constraints:
        unsupported.append("The English Constraints section is empty")
        return result()
    input_headings = [
        heading
        for heading in english.find_all("h3")
        if heading.get_text(" ", strip=True) == "Input"
    ]
    if len(input_headings) != 1:
        unsupported.append("A single English Input section is required")
        return result()
    try:
        # Restrict oj-template to the English subtree; it otherwise picks the
        # first matching heading, which can belong to the Japanese statement.
        input_format = oj_html.parse_input_format_string(str(english).encode("utf-8"), url=url)
        size_name, values_name, shape, *fields = _input_shape(input_format)
    except Exception as error:
        unsupported.append(f"Unsupported input format: {error or type(error).__name__}")
        return result()

    if shape == "graph":
        return result(
            _graph_spec(
                constraints,
                recognized,
                unsupported,
                vertex_name=size_name,
                edge_name=values_name,
                fields=fields,
                max_size=max_size,
                url=url,
            )
        )

    if shape == "string":
        return result(
            _string_spec(
                constraints,
                recognized,
                unsupported,
                size_name=size_name,
                string_name=values_name,
                max_size=max_size,
                url=url,
            )
        )

    bounds: dict[tuple[str, str], list[int | str]] = {
        (target, side): [] for target in ("size", "values") for side in ("lower", "upper")
    }
    integers = False
    min_distinct = 1
    for clause in constraints:
        try:
            if _INTEGER_CLAUSE.fullmatch(clause):
                integers = True
            elif _distinct_clause(clause, size_name, values_name):
                min_distinct = 2
            else:
                for target, side, endpoint in _bound_clause(clause, size_name, values_name):
                    bounds[target, side].append(endpoint)
            recognized.append(clause)
        except UnsupportedConstraint as error:
            unsupported.append(f"{clause}: {error}")
    if not integers:
        unsupported.append("Missing an explicit clause declaring all input values to be integers")
    for (target, side), entries in bounds.items():
        if not entries:
            unsupported.append(f"Missing {side} bound for {target}")
    if unsupported:
        return result()
    try:
        size_min = max(bounds["size", "lower"])
        size_max = min(bounds["size", "upper"])
        spec = IntegerArrayConstraints(
            size_name=size_name,
            size_min=size_min,
            size_max=size_max,
            values_name=values_name,
            values_min=_combined_bound(
                bounds["values", "lower"],
                lower=True,
                size_name=size_name,
                size_min=size_min,
                size_max=size_max,
            ),
            values_max=_combined_bound(
                bounds["values", "upper"],
                lower=False,
                size_name=size_name,
                size_min=size_min,
                size_max=size_max,
            ),
            min_distinct=min_distinct,
            stress_max_size=max_size,
            url=url,
        )
    except ValueError as error:
        unsupported.append(f"Constraints cannot be represented by the supported model: {error}")
        return result()
    return result(spec)
