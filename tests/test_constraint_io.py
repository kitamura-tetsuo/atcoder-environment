"""Load explicit string and legacy array schemas without dropping conditions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from atcoder_env.constraint_io import load_constraints
from atcoder_env.constraint_model import IntegerArrayConstraints
from atcoder_env.string_constraints import StringConstraints

STRING = StringConstraints("N", 3, 300_000, "S", "ABC")


def save(tmp_path, text):
    path = tmp_path / "constraints.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_string_and_legacy_array_round_trip_without_schema_conversion(tmp_path):
    array = IntegerArrayConstraints("N", 2, 300_000, "C", 1, "N", min_distinct=2)
    for model in (STRING, array):
        path = save(tmp_path, model.to_toml())
        assert load_constraints(path) == model
        assert type(load_constraints(path)) is type(model)
    punctuation = replace(STRING, alphabet='aZ0"\\', url='https://example.test/?q="x"')
    path = save(tmp_path, punctuation.to_toml())
    assert load_constraints(path) == punctuation
    assert StringConstraints.from_toml(path) == punctuation


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace("version = 1", "version = 2"),
        lambda text: text.replace("version = 1", "version = true"),
        lambda text: text.replace("version = 1", "version = 1\nignored = true"),
        lambda text: text.replace("[size]", "[size]\nunused = 5"),
        lambda text: text.replace("[string]", "[string]\nmin_distinct = 3"),
        lambda text: text.replace('alphabet = "ABC"\n', ""),
        lambda text: text.replace('alphabet = "ABC"', 'alphabet = ["A", "B", "C"]'),
        lambda text: text.replace("[problem]", "[problem]\nkind = 'string'"),
        lambda text: text.replace("[testing]", "[testing]\nunknown = 3"),
        lambda text: text.replace("max_size = 30", "max_size = 1"),
        lambda text: text + "\n[extra]\ncondition = true\n",
        lambda text: text + '\n[values]\nname = "A"\nmin = 0\nmax = 1\n',
    ],
)
def test_unknown_ambiguous_missing_and_invalid_conditions_fail(tmp_path, change):
    with pytest.raises(ValueError):
        load_constraints(save(tmp_path, change(STRING.to_toml())))


@pytest.mark.parametrize("content", ["version = 1\n", "[size\n", "string = 'ABC'\n"])
def test_no_supported_model_or_invalid_toml_fails(tmp_path, content):
    with pytest.raises(ValueError):
        load_constraints(save(tmp_path, content))


def test_model_specific_loaders_cannot_misinterpret_the_other_shape(tmp_path):
    with pytest.raises(ValueError, match="Unknown constraints keys"):
        IntegerArrayConstraints.from_toml(save(tmp_path, STRING.to_toml()))
    array = IntegerArrayConstraints("N", 2, 300_000, "C", 1, "N", min_distinct=2)
    with pytest.raises(ValueError, match="Unknown constraints keys"):
        StringConstraints.from_toml(save(tmp_path, array.to_toml()))
