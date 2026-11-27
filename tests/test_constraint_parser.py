"""Verify constraint extraction without guessing types, ranges, or extra rules."""

from __future__ import annotations

import html as html_module
from pathlib import Path

import pytest

from atcoder_env.constraint_parser import analyze_statement

URL = "https://atcoder.jp/contests/abc485/tasks/abc485_c"
FIXTURE = Path(__file__).with_name("fixtures") / "abc485_c_constraints.html"


def statement(
    *,
    size: str = r"2 \le N \le 10",
    values: str = r"1 \le C_i \le N",
    extras: tuple[str, ...] = (),
    integer_clause: str = "All input values are integers.",
    input_format: str = "N\nC_1 C_2 \\ldots C_N\n",
) -> bytes:
    clauses = [size, values, *extras, integer_clause]
    items = "".join(f"<li>{html_module.escape(clause)}</li>" for clause in clauses if clause)
    return (
        '<div id="task-statement"><span class="lang-en">'
        f"<section><h3>Constraints</h3><ul>{items}</ul></section>"
        f"<section><h3>Input</h3><pre>{html_module.escape(input_format)}</pre></section>"
        "</span></div>"
    ).encode()


def test_exact_abc485c_constraints_are_fully_extracted() -> None:
    analysis = analyze_statement(FIXTURE.read_bytes(), URL)
    assert analysis.unsupported == []
    assert len(analysis.constraints) == len(analysis.recognized) == 4
    assert analysis.input_format is not None
    spec = analysis.spec
    assert spec is not None
    assert (spec.size_name, spec.size_min, spec.size_max) == ("N", 2, 300_000)
    assert (spec.values_name, spec.values_min, spec.values_max) == ("C", 1, "N")
    assert spec.min_distinct == 2
    assert spec.stress_max_size == 30
    assert spec.url == URL
    for case in spec.edge_cases():
        assert len(set(case)) >= 2
        assert 2 <= len(case) <= 30
        assert all(1 <= value <= len(case) for value in case)


def test_english_input_is_used_even_if_japanese_input_appears_first() -> None:
    japanese = b'<span class="lang-ja"><section><h3>Input</h3><pre>N M\nX\n</pre></section></span>'
    analysis = analyze_statement(japanese + FIXTURE.read_bytes(), URL)
    assert analysis.spec is not None
    assert analysis.unsupported == []
    assert analysis.spec.values_name == "C"


@pytest.mark.parametrize(
    "source",
    [
        b"<html><title>Sign in</title></html>",
        statement().replace(b"lang-en", b"lang-ja"),
        statement().replace(b"<h3>Constraints</h3>", b"<h3>Explanation</h3>"),
        statement().replace(b"<h3>Input</h3>", b"<h3>Output</h3>"),
        statement() + statement(),
        statement().replace(b"</span>", b"<section><h3>Input</h3><pre>X</pre></section></span>"),
    ],
)
def test_missing_or_ambiguous_english_sections_fail_closed(source: bytes) -> None:
    result = analyze_statement(source, URL)
    assert result.spec is None
    assert result.unsupported


@pytest.mark.parametrize(
    "condition",
    [
        "The sum of the values is even.",
        "All colors are pairwise distinct.",
        "The values form a permutation.",
        "The graph is connected.",
    ],
)
def test_unknown_mandatory_condition_blocks_partial_generator(condition: str) -> None:
    result = analyze_statement(statement(extras=(condition,)), URL)
    assert result.spec is None
    assert any(condition in message for message in result.unsupported)
    assert len(result.recognized) == 3


def test_mandatory_prose_outside_list_is_not_discarded() -> None:
    source = statement().replace(b"</ul>", b"</ul><p>The total is at most ten.</p>")
    result = analyze_statement(source, URL)
    assert result.spec is None
    assert "The total is at most ten." in result.constraints


@pytest.mark.parametrize("integer_clause", ["", "All input values are real numbers."])
def test_integer_type_is_never_inferred_from_samples_or_bounds(integer_clause: str) -> None:
    result = analyze_statement(statement(integer_clause=integer_clause), URL)
    assert result.spec is None
    assert any("integers" in reason for reason in result.unsupported)


@pytest.mark.parametrize(
    ("size", "minimum", "maximum"),
    [
        ("1 < N < 6", 2, 5),
        ("6 > N > 1", 2, 5),
        ("2 <= N < 6", 2, 5),
        ("1 < N <= 5", 2, 5),
        (r"\frac{1}{2} < N < \frac{11}{2}", 1, 5),
        (r"2 \leq N \leq 3 \times 10^5", 2, 300_000),
        ("2 ≤ N ≤ 3 × 10^5", 2, 300_000),
    ],
)
def test_integer_endpoints_honor_strict_and_inclusive_relations(
    size: str, minimum: int, maximum: int
) -> None:
    result = analyze_statement(statement(size=size), URL)
    assert result.spec is not None, result.unsupported
    assert result.spec.size_min == minimum
    assert result.spec.size_max == maximum


def test_negative_strict_element_bounds_are_rounded_correctly() -> None:
    result = analyze_statement(statement(values="-3 < C_i < 4"), URL)
    assert result.spec is not None, result.unsupported
    assert result.spec.values_min == -2
    assert result.spec.values_max == 3


def test_separate_binary_constraints_merge_without_inventing_defaults() -> None:
    result = analyze_statement(
        statement(size="2 <= N", values="1 <= C_i", extras=("N <= 10", "C_i <= N")), URL
    )
    assert result.spec is not None, result.unsupported
    assert result.spec.size_min == 2
    assert result.spec.size_max == 10
    assert result.spec.values_min == 1
    assert result.spec.values_max == "N"


@pytest.mark.parametrize(
    ("size", "values"),
    [
        ("N <= 10", "1 <= C_i <= N"),
        ("2 <= N <= 10", "C_i <= N"),
        ("2 <= N <= 1", "1 <= C_i <= N"),
        ("2 <= N <= 10", "3 <= C_i <= 1"),
        ("2 <= N <= M", "1 <= C_i <= N"),
        ("2 <= N <= 10", "1 <= C_i < N"),
        ("2 <= N <= 10", "1 <= C_i <= N-1"),
        ("2 <= N <= 10", "1 <= C_1 <= N"),
        ("2 <= N <= 10", "1 <= C_N <= N"),
        ("2 <= N <= 10", "1 <= C_C <= N"),
        ("2 <= N <= 10", "1 <= D_i <= N"),
        ("2 <= N <= 10", "C_i <= C_j"),
    ],
)
def test_missing_infeasible_or_unrepresentable_bounds_fail_closed(size: str, values: str) -> None:
    result = analyze_statement(statement(size=size, values=values), URL)
    assert result.spec is None
    assert result.unsupported


@pytest.mark.parametrize(
    "upper",
    [
        "M",
        "2e5",
        "f(4)",
        r"\sin{4}",
        r"10^{100000000}",
        r"10^{19}",
        r"10^{2+3}",
        "10000000000000000000",
        "1/0",
        "10 -",
        "__import__('os')",
        "(" * 20 + "10" + ")" * 20,
        "1+" * 150 + "10",
        r"\leftN",
    ],
)
def test_malformed_unknown_or_resource_heavy_math_is_rejected(upper: str) -> None:
    result = analyze_statement(statement(size=f"2 <= N <= {upper}"), URL)
    assert result.spec is None
    assert result.unsupported


def test_supported_large_integer_does_not_lose_precision() -> None:
    result = analyze_statement(statement(values=r"-10^{18} <= C_i <= 10^{18}"), URL)
    assert result.spec is not None, result.unsupported
    assert result.spec.values_min == -(10**18)
    assert result.spec.values_max == 10**18


@pytest.mark.parametrize("domain", ["1 <= i <= N", "N >= i >= 1", "0 < i <= N"])
def test_index_annotations_are_verified_against_complete_array(domain: str) -> None:
    result = analyze_statement(statement(values=f"1 <= C_i <= N ({domain})"), URL)
    assert result.spec is not None, result.unsupported


@pytest.mark.parametrize(
    "domain", ["1 <= i < N", "2 <= i <= N", "1 <= j <= N", "1 <= i <= 10", "i <= N"]
)
def test_partial_or_wrong_index_annotations_are_not_ignored(domain: str) -> None:
    result = analyze_statement(statement(values=f"1 <= C_i <= N ({domain})"), URL)
    assert result.spec is None
    assert result.unsupported


@pytest.mark.parametrize(
    "input_format",
    [
        "N M\nC_1 C_2 \\ldots C_N\n",
        "N\nC_1 C_2 \\ldots C_M\n",
        "N\nC_0 C_1 \\ldots C_{N-1}\n",
        "N\nC_1\nC_2\n\\vdots\nC_N\n",
        "N\nC\n",
        "N\n",
    ],
)
def test_different_input_shape_cannot_receive_array_generator(input_format: str) -> None:
    result = analyze_statement(statement(input_format=input_format), URL)
    assert result.spec is None
    assert any("input format" in reason for reason in result.unsupported)


def test_distinctness_must_reference_the_whole_declared_array() -> None:
    source = FIXTURE.read_bytes().replace(b"C_1, C_2, \\ldots, C_N", b"D_1, D_2, \\ldots, D_N")
    result = analyze_statement(source, URL)
    assert result.spec is None
    assert any("Distinctness" in reason for reason in result.unsupported)


def test_crossing_symbolic_and_constant_bounds_are_not_weakened() -> None:
    result = analyze_statement(statement(extras=("C_i <= 5",)), URL)
    assert result.spec is None
    assert any("min/max" in reason for reason in result.unsupported)


def test_redundant_constant_bound_is_safe_to_combine() -> None:
    result = analyze_statement(statement(extras=("C_i <= 20",)), URL)
    assert result.spec is not None, result.unsupported
    assert result.spec.values_max == "N"


def test_stress_size_cap_is_not_silently_raised() -> None:
    result = analyze_statement(statement(size="40 <= N <= 50"), URL, max_size=30)
    assert result.spec is None
    assert any("stress_max_size" in reason for reason in result.unsupported)
    result = analyze_statement(statement(size="40 <= N <= 50"), URL, max_size=45)
    assert result.spec is not None
    assert result.spec.stress_max_size == 45


@pytest.mark.parametrize("cap", [0, -1, True, 1.5])
def test_invalid_stress_caps_are_reported(cap: object) -> None:
    result = analyze_statement(statement(), URL, max_size=cap)
    assert result.spec is None
    assert result.unsupported


def string_statement(
    *,
    size: str = "3 <= N <= 300000",
    string_clause: str = "S is a string of length N consisting of A, B, and C.",
    integer_clause: str = "N is an integer.",
    input_format: str = "N\nS\n",
    extras: tuple[str, ...] = (),
) -> bytes:
    return statement(
        size=size,
        values=string_clause,
        integer_clause=integer_clause,
        input_format=input_format,
        extras=extras,
    )


def test_exact_abc485d_constraints_create_a_string_model() -> None:
    from atcoder_env.string_constraints import StringConstraints

    url = "https://atcoder.jp/contests/abc485/tasks/abc485_d"
    fixture = FIXTURE.with_name("abc485_d_constraints.html")
    result = analyze_statement(fixture.read_bytes(), url)
    assert result.unsupported == []
    assert len(result.constraints) == len(result.recognized) == 3
    spec = result.spec
    assert isinstance(spec, StringConstraints)
    assert (spec.size_name, spec.size_min, spec.size_max) == ("N", 3, 300000)
    assert spec.string_name == "S"
    assert spec.alphabet == "ABC"
    assert spec.stress_max_size == 30
    assert spec.url == url
    for case in spec.edge_cases():
        size, value = spec.serialize(case).splitlines()
        assert int(size) == len(value)
        assert 3 <= len(value) <= 30
        assert set(value) <= set("ABC")


def test_string_variables_and_alphabet_are_taken_from_the_statement() -> None:
    result = analyze_statement(
        string_statement(
            size="1 < M < 9",
            string_clause="T is a string of length M consisting of x, y and z.",
            integer_clause="M is an integer.",
            input_format="M\nT\n",
        ),
        URL,
    )
    assert result.spec is not None, result.unsupported
    assert (result.spec.size_name, result.spec.size_min, result.spec.size_max) == ("M", 2, 8)
    assert result.spec.string_name == "T"
    assert result.spec.alphabet == "xyz"


@pytest.mark.parametrize(
    ("alphabet_clause", "alphabet"),
    [
        ("A , B , and C", "ABC"),
        ("A, B and C", "ABC"),
        ("A, B, C", "ABC"),
        ("A and B", "AB"),
        ("A", "A"),
        ("`0` and `1`", "01"),
        ("'#' and '.'", "#."),
        ('"x", "y", and "z"', "xyz"),
        ("'~', '\\', and '*'", "~\\*"),
        ("',' and '.'", ",."),
    ],
)
def test_literal_alphabets_are_fully_consumed_and_preserved(
    alphabet_clause: str, alphabet: str
) -> None:
    result = analyze_statement(
        string_statement(
            string_clause=f"S is a string of length N consisting of {alphabet_clause}."
        ),
        URL,
    )
    assert result.spec is not None, result.unsupported
    assert result.spec.alphabet == alphabet


@pytest.mark.parametrize(
    "clause",
    [
        "S is a string of length M consisting of A, B, and C.",
        "S is a string of length N+1 consisting of A, B, and C.",
        "S is a string of length 2N consisting of A, B, and C.",
        "T is a string of length N consisting of A, B, and C.",
        "S is a string consisting of A, B, and C.",
        "S is a string of length N consisting of lowercase English letters.",
        "S is a string of length N consisting of ABC.",
        "S is a string of length N consisting of 'ABC'.",
        "S is a string of length N consisting of A-Z.",
        "S is a string of length N consisting of .",
        "S is a string of length N consisting of A, B, .",
        "S is a string of length N consisting of A and B and C.",
        "S is a string of length N consisting of A, B, or C.",
        "S is a string of length N consisting of A, A, and C.",
        "S is a string of length N consisting of α and β.",
        "S is a string of length N consisting of `−`.",
        "S is a string of length N consisting of A, B, and C. S contains A.",
        "S is a string of length N consisting of A, B, and C, with at least two distinct characters.",
    ],
)
def test_unsupported_string_length_alphabet_or_extra_prose_fails_closed(clause: str) -> None:
    result = analyze_statement(string_statement(string_clause=clause), URL)
    assert result.spec is None
    assert result.unsupported
    assert any(clause in reason for reason in result.unsupported)


@pytest.mark.parametrize(
    "input_format",
    [
        "N S\n",
        "N\nS\nT\n",
        "N\nS_1\n",
        "N\nS_1 S_2 \\ldots S_N\n",
        "S\nN\n",
        "N\nN\n",
    ],
)
def test_string_constraints_require_the_declared_two_scalar_input_shape(input_format: str) -> None:
    result = analyze_statement(string_statement(input_format=input_format), URL)
    assert result.spec is None
    assert result.unsupported


@pytest.mark.parametrize(
    "integer_clause",
    [
        "",
        "M is an integer.",
        "N is a real number.",
        "All input values are integers.",
        "N is an integer. N is even.",
    ],
)
def test_string_length_type_requires_its_own_complete_integer_declaration(
    integer_clause: str,
) -> None:
    result = analyze_statement(string_statement(integer_clause=integer_clause), URL)
    assert result.spec is None
    assert any("declaring N to be an integer" in reason for reason in result.unsupported)


@pytest.mark.parametrize(
    "condition",
    [
        "S contains at least one A.",
        "S is a palindrome.",
        "N is odd.",
        "1 <= S_i <= N",
        "S is a string of length N consisting of A and B.",
    ],
)
def test_additional_string_conditions_block_a_partial_generator(condition: str) -> None:
    result = analyze_statement(string_statement(extras=(condition,)), URL)
    assert result.spec is None
    assert any(condition in reason for reason in result.unsupported)
    assert len(result.recognized) == 3


@pytest.mark.parametrize("size", ["N <= 10", "3 <= N", "3 <= N <= M", "4 <= N <= 2"])
def test_string_length_bounds_are_required_and_must_be_representable(size: str) -> None:
    result = analyze_statement(string_statement(size=size), URL)
    assert result.spec is None
    assert result.unsupported


def test_string_alphabet_is_not_inferred_from_sample_input() -> None:
    source = string_statement(string_clause="").replace(
        b"</span>",
        b"<section><h3>Sample Input 1</h3><pre>3\nABC\n</pre></section></span>",
    )
    result = analyze_statement(source, URL)
    assert result.spec is None
    assert any("literal alphabet declaration" in reason for reason in result.unsupported)
