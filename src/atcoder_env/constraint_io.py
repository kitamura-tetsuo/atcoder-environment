"""Select a strictly validated input model from its explicit constraint schema."""

from __future__ import annotations

import tomllib
from pathlib import Path

from atcoder_env.constraint_model import IntegerArrayConstraints
from atcoder_env.graph_constraints import GraphConstraints
from atcoder_env.string_constraints import StringConstraints


def load_constraints(path: Path) -> IntegerArrayConstraints | StringConstraints | GraphConstraints:
    """Load exactly one supported input kind while preserving legacy array files."""
    with Path(path).open("rb") as stream:
        data = tomllib.load(stream)
    kinds = {
        "values": IntegerArrayConstraints,
        "string": StringConstraints,
        "edges": GraphConstraints,
    }
    selected = [model for key, model in kinds.items() if key in data]
    if len(selected) != 1:
        raise ValueError("Constraints must declare exactly one of [string], [values], or [edges]")
    model = selected[0]
    return model.from_toml(path)
