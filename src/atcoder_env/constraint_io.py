"""Select a strictly validated input model from its explicit constraint schema."""

from __future__ import annotations

import tomllib
from pathlib import Path

from atcoder_env.constraint_model import IntegerArrayConstraints
from atcoder_env.string_constraints import StringConstraints


def load_constraints(path: Path) -> IntegerArrayConstraints | StringConstraints:
    """Load exactly one supported input kind while preserving legacy array files."""
    with Path(path).open("rb") as stream:
        data = tomllib.load(stream)
    if ("string" in data) == ("values" in data):
        raise ValueError("Constraints must declare exactly one of [string] or [values]")
    model = StringConstraints if "string" in data else IntegerArrayConstraints
    return model.from_toml(path)
