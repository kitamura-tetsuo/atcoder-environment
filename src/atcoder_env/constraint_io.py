"""Select a strictly validated input model from its explicit constraint schema."""

from __future__ import annotations

import tomllib
from pathlib import Path

from atcoder_env.constraint_model import IntegerArrayConstraints
from atcoder_env.graph_constraints import GraphConstraints
from atcoder_env.official_corpus import OfficialCorpus
from atcoder_env.string_constraints import StringConstraints
from atcoder_env.structured_constraints import StructuredConstraints


def load_constraints(
    path: Path,
) -> (
    IntegerArrayConstraints
    | StringConstraints
    | GraphConstraints
    | StructuredConstraints
    | OfficialCorpus
):
    """Load exactly one supported input kind while preserving legacy array files."""
    with Path(path).open("rb") as stream:
        data = tomllib.load(stream)
    kinds = {
        "values": IntegerArrayConstraints,
        "string": StringConstraints,
        "edges": GraphConstraints,
        "structured": StructuredConstraints,
        "official_corpus": OfficialCorpus,
    }
    selected = [model for key, model in kinds.items() if key in data]
    if len(selected) != 1:
        raise ValueError(
            "Constraints must declare exactly one of [string], [values], [edges], [structured], or [official_corpus]"
        )
    model = selected[0]
    return model.from_toml(path)
