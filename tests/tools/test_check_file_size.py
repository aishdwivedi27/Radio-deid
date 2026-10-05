"""tools/check_file_size.py fails on a planted violation (CLAUDE.md, File size)."""

from pathlib import Path

from tools import check_file_size


def _write(path: Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"x{i} = {i}\n" for i in range(lines)), encoding="utf-8")


def test_over_hard_cap_fails(tmp_path: Path) -> None:
    _write(tmp_path / "app" / "big.py", 1001)
    assert check_file_size.main([str(tmp_path)]) == 1


def test_over_target_warns_only(tmp_path: Path) -> None:
    _write(tmp_path / "app" / "medium.py", 401)
    failures, warnings = check_file_size.check(tmp_path)
    assert not failures and any("medium.py" in w for w in warnings)
    assert check_file_size.main([str(tmp_path)]) == 0


def test_long_function_warns(tmp_path: Path) -> None:
    body = "".join(f"    a{i} = {i}\n" for i in range(61))
    (tmp_path / "f.py").write_text(f"def big():\n{body}", encoding="utf-8")
    _, warnings = check_file_size.check(tmp_path)
    assert any("function big" in w for w in warnings)


def test_exempt_paths_ignored(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "fixtures" / "gen.py", 1500)
    _write(tmp_path / "app" / "store" / "migrations" / "versions" / "0001.py", 1500)
    _write(tmp_path / "node_modules" / "lib.js", 1500)
    assert check_file_size.main([str(tmp_path)]) == 0


def test_repo_passes() -> None:
    assert check_file_size.main([]) == 0
