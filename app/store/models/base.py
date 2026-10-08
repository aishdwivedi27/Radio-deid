"""Declarative base. Times are ISO 8601 strings with offset (the same text that goes into the rows)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
