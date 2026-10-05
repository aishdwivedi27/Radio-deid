"""Shared fixtures. Synthetic data only (SYNTHETIC-DEMO / DEMO-)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Planted identifiers from reference/test_end_to_end.py: the fake patient details in the sample inbox.
PHI = [
    "Sharma",
    "Ramesh",
    "UHID-778812",
    "Iyer",
    "Lakshmi",
    "UHID-903311",
    "Patel",
    "Ananya",
    "UHID-440021",
    "98220 45671",
    "9444012345",
    "9839001122",
    "4821 7730 1192",
    "Kulkarni",
    "Deshpande",
    "Sunrise Diagnostics",
    "Menon",
    "Verma",
    "Gupta",
]


@pytest.fixture
def repo() -> Path:
    return REPO
