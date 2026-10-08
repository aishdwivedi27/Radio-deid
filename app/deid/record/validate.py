"""Validate each line before it is appended (SPEC §5.2: every JSONL line is validated). TR-DEID-10.

Error messages carry the JSON path and the failing keyword only, never the value.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator

from app.deid.schemas import row_schema
from app.deid.types import DeidError


@cache
def _validator(kind: str) -> Draft202012Validator:
    return Draft202012Validator(row_schema(kind))


def validate_row(kind: str, row: Mapping[str, Any]) -> None:
    errors = sorted(_validator(kind).iter_errors(dict(row)), key=lambda e: list(e.absolute_path))
    if errors:
        where = ", ".join(f"/{'/'.join(map(str, e.absolute_path))} ({e.validator})" for e in errors[:5])
        raise DeidError(f"{kind} row fails its schema: {where}")
