"""Relative paths sent with an upload (SPEC §7 staging). TR-ING-01.

A folder drop sends each file with its path relative to the dropped folder. The path is split on both
separators and every part is checked, so nothing can climb out of ``staging/<job_id>/files/`` or name a
device. Rejections carry a reason code only, never the path (it may contain a patient's name).
"""

from __future__ import annotations

import re

MAX_DEPTH = 32
MAX_PART = 255
MAX_TOTAL = 1024
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(\..*)?$", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BAD_CHARS = re.compile(r'[<>:"|?*]')


class PathRejected(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _check_part(part: str) -> None:
    if part in ("", "."):
        raise PathRejected("empty_part")
    if part == "..":
        raise PathRejected("dotdot")
    if _CONTROL.search(part):
        raise PathRejected("control_char")
    if _BAD_CHARS.search(part):
        raise PathRejected("bad_char")  # also refuses drive letters ("C:") and alternate data streams
    if part != part.rstrip(". "):
        raise PathRejected("trailing_dot_or_space")
    if _RESERVED.match(part):
        raise PathRejected("reserved_name")
    if len(part) > MAX_PART:
        raise PathRejected("too_long")


def sanitise_relative(raw: str) -> tuple[str, ...]:
    """The parts of a safe relative path, or ``PathRejected``."""
    if not raw or len(raw) > MAX_TOTAL:
        raise PathRejected("empty" if not raw else "too_long")
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise PathRejected("absolute")
    parts = tuple(re.split(r"[\\/]", raw))
    if len(parts) > MAX_DEPTH:
        raise PathRejected("too_deep")
    for part in parts:
        _check_part(part)
    return parts


def has_dotdot(raw: str) -> bool:
    return ".." in re.split(r"[\\/]", raw)
