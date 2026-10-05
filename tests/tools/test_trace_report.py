"""tools/trace_report.py: phase-gated failure on untested TRs (DECISIONS D-009)."""

import zipfile
from pathlib import Path

from tools import trace_report


def _fake_repo(tmp_path: Path, test_tags: str) -> Path:
    (tmp_path / "docs").mkdir()
    with zipfile.ZipFile(tmp_path / "docs" / "TRS-RAD-001_Technical_Requirements_v9.docx", "w") as zf:
        zf.writestr("word/document.xml", "<w:p>TR-AA-01</w:p><w:p>TR-AA-02</w:p><w:p>TR-BB-01</w:p>")
    (tmp_path / "docs" / "SPEC.md").write_text("## 3. Thing\nRule [TR-AA-01..02]\n", encoding="utf-8")
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "phase-0-x.md").write_text("**Implements:** TR-AA-01..02\n", encoding="utf-8")
    (tmp_path / "prompts" / "phase-1-y.md").write_text("**Implements:** TR-BB-01\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(f"# {test_tags}\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    return tmp_path


def test_expand_range() -> None:
    assert trace_report.expand("TR-ZZ-01..03") == ["TR-ZZ-01", "TR-ZZ-02", "TR-ZZ-03"]
    assert trace_report.find_trs("x TR-ZZ-NF-01 y") == {"TR-ZZ-NF-01"}


def test_due_tr_without_test_fails(tmp_path: Path) -> None:
    root = _fake_repo(tmp_path, "TR-AA-01")  # TR-AA-02 (phase 0) untested
    assert trace_report.main(["--phase", "0", "--root", str(root)]) == 1


def test_later_phase_tr_is_pending(tmp_path: Path) -> None:
    root = _fake_repo(tmp_path, "TR-AA-01..02")  # TR-BB-01 is phase 1
    assert trace_report.main(["--phase", "0", "--root", str(root)]) == 0
    assert trace_report.main(["--phase", "1", "--root", str(root)]) == 1
    assert trace_report.main(["--strict", "--root", str(root)]) == 1


def test_skips_without_docs(tmp_path: Path) -> None:
    assert trace_report.main(["--root", str(tmp_path)]) == 0
