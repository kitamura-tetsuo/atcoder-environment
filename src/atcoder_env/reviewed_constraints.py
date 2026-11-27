"""Reviewed structured recipes for recent statements with complex constraints.

Only use a recipe when the official English statement text still matches its
reviewed fingerprint. Never run downloaded code or assume a changed problem
has the same constraints.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.resources import files

from bs4 import Tag

from atcoder_env.external import task_parts
from atcoder_env.structured_constraints import StructuredConstraints


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads(files("atcoder_env").joinpath("recent_abc.json").read_text(encoding="utf-8"))


def statement_digest(english: Tag) -> str:
    canonical = " ".join(english.get_text(" ", strip=True).split())
    return hashlib.sha256(canonical.encode()).hexdigest()


def lookup(english: Tag, url: str, *, max_size: int) -> StructuredConstraints | None:
    try:
        contest, task = task_parts(url)
    except ValueError:
        return None
    recipe = catalog().get(task)
    if recipe is None or contest != task.split("_")[0]:
        return None
    if statement_digest(english) != recipe["statement_sha256"]:
        return None
    return StructuredConstraints(
        size_name=recipe["size_name"],
        size_min=recipe["size_min"],
        size_max=recipe["size_max"],
        steps=recipe["steps"],
        stress_max_size=max_size,
        url=url,
        primary_step=recipe["primary_step"],
    )
