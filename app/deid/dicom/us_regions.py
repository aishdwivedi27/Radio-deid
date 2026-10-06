"""Rebuild SequenceOfUltrasoundRegions with only the allowed item elements (SPEC §6.0). TR-DEID-01."""

from __future__ import annotations

from pydicom.dataset import Dataset
from pydicom.sequence import Sequence


def rebuild_regions(src: Sequence, item_keywords: tuple[str, ...]) -> Sequence:
    """New items holding only allowed non-sequence elements; private tags and nested sequences dropped."""
    items = []
    for old in src:
        new = Dataset()
        for kw in item_keywords:
            if kw in old and old[kw].VR != "SQ":
                el = old[kw]
                new.add_new(el.tag, el.VR, el.value)
        items.append(new)
    return Sequence(items)
