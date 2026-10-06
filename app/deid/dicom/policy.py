"""Per-modality element policy, built only from ``docs/schemas/dicom_allowlist.json`` (SPEC §6.0). TR-DEID-01.

Code works with keywords; the tag numbers live only in the JSON. Any handling code this module does not know
stops processing (fail closed), so a new code in the JSON cannot silently become "copy".
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydicom.datadict import tag_for_keyword

from app.deid.schemas import allowlist

HANDLING = frozenset({"copy", "scrub", "recompute", "validate", "mask", "set_after_mask", "item_allowlist"})


class PolicyError(ValueError):
    """The allowlist JSON and this code disagree."""


@dataclass(frozen=True)
class Policy:
    modality: str
    profile: str | None
    handling: dict[str, str]  # keyword -> handling code, core ∪ profile
    generated: tuple[str, ...]  # keywords written by the app (Table B)
    us_items: tuple[str, ...]  # keywords allowed inside a rebuilt ultrasound region item

    @property
    def allowed_keywords(self) -> frozenset[str]:
        return frozenset(self.handling) | frozenset(self.generated)

    @property
    def allowed_tags(self) -> frozenset[int]:
        return frozenset(keyword_tag(k) for k in self.allowed_keywords)

    @property
    def sequence_keywords(self) -> frozenset[str]:
        return frozenset(k for k, h in self.handling.items() if h == "item_allowlist")


def keyword_tag(keyword: str) -> int:
    tag = tag_for_keyword(keyword)
    if tag is None:
        raise PolicyError("allowlist keyword unknown to pydicom")
    return int(tag)


def _check(entries: tuple[dict[str, str], ...]) -> None:
    for e in entries:
        if e.get("handling", "copy") not in HANDLING:
            raise PolicyError(f"unknown handling code for {e.get('keyword')}")
        json_tag = int(e["tag"][1:5] + e["tag"][6:10], 16)
        if keyword_tag(e["keyword"]) != json_tag:
            raise PolicyError(f"tag and keyword disagree for {e.get('keyword')}")


@lru_cache(maxsize=16)
def policy_for(modality: str) -> Policy:
    al = allowlist()
    profile = al.modality_to_profile.get(modality.upper())
    entries = (*al.core, *(al.profiles.get(profile, ()) if profile else ()))
    _check(entries)
    _check(al.generated)
    return Policy(
        modality=modality.upper(),
        profile=profile,
        handling={e["keyword"]: e["handling"] for e in entries},
        generated=tuple(e["keyword"] for e in al.generated),
        us_items=tuple(e["keyword"] for e in al.us_region_items),
    )


def all_profile_keywords() -> dict[str, frozenset[str]]:
    """Profile name -> its keywords (used by A1 to name a field from another modality's profile)."""
    return {name: frozenset(e["keyword"] for e in items) for name, items in allowlist().profiles.items()}
