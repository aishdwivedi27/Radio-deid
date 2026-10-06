"""De-identification numbers (SPEC §3, DECISIONS D-016). TR-DEID-09."""

import re

from app.deid import pseudonyms as ps
from app.deid.index import index_input

KEY_A, KEY_B = b"a" * 32, b"b" * 32


def test_formats() -> None:  # TR-DEID-09
    assert re.fullmatch(r"P[0-9A-F]{12}", ps.patient_code(KEY_A, "DEMO-1"))
    assert re.fullmatch(r"S[0-9A-F]{12}", ps.record_id(KEY_A, "1.2.3"))
    uid = ps.pseudo_uid(KEY_A, "1.2.3.4")
    assert uid.startswith("2.25.") and len(uid) <= 64 and re.fullmatch(r"[0-9.]+", uid)
    assert 1 <= ps.shift_days(KEY_A, "DEMO-1") <= 365


def test_deterministic_and_key_dependent() -> None:  # TR-DEID-09
    for fn in (ps.patient_code, ps.record_id, ps.pseudo_uid, ps.shift_days):
        assert fn(KEY_A, "x1") == fn(KEY_A, "x1")
    assert ps.patient_code(KEY_A, "x1") != ps.patient_code(KEY_B, "x1")
    assert ps.record_id(KEY_A, "x1") != ps.record_id(KEY_B, "x1")
    assert ps.pseudo_uid(KEY_A, "x1") != ps.pseudo_uid(KEY_B, "x1")


def test_sample_inbox_ids_repeatable(sample_inbox, key) -> None:  # type: ignore[no-untyped-def]  # TR-DEID-09
    first = [ps.record_id(key, g.study_uid) for g in index_input(sample_inbox).studies]
    second = [ps.record_id(key, g.study_uid) for g in index_input(sample_inbox).studies]
    assert first == second and len(set(first)) == 4


def test_image_id_width_and_suffix() -> None:  # TR-DEID-09, D-016
    alloc = ps.ImageIdAllocator("S0123456789AB")
    assert alloc.allocate(2, 1) == "S0123456789AB-0002-000001"
    assert alloc.allocate(2, 1) == "S0123456789AB-0002-000001-b"
    assert alloc.allocate(None, None) == "S0123456789AB-0000-000000"
    assert alloc.truncated == 0


def test_image_id_keeps_rightmost_digits() -> None:  # TR-DEID-09, D-016
    alloc = ps.ImageIdAllocator("S0123456789AB")
    assert alloc.allocate(2345, 1) == "S0123456789AB-2345-000001"
    assert alloc.allocate(12345, 1) == "S0123456789AB-2345-000001-b"  # 12345 -> 2345, collides -> -b
    assert alloc.allocate(7, 1234567) == "S0123456789AB-0007-234567"
    assert alloc.truncated == 2
    again = ps.ImageIdAllocator("S0123456789AB")
    assert [again.allocate(2345, 1), again.allocate(12345, 1)] == [
        "S0123456789AB-2345-000001",
        "S0123456789AB-2345-000001-b",
    ]
