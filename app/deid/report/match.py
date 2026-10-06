"""Match reports to studies by ID only (SPEC §6.2; CLAUDE.md rule 16). TR-RPT-03, TR-RPT-06.

Tiers, in order:
1. the study's accession number appears in the report's file name as a whole token;
2. the report's folder holds DICOM files of exactly one study;
3. the study's patient ID appears in the file name as a whole token, and that patient has exactly one
   study in the batch.
Never by name, age, sex, referring doctor, report date or exam type, never fuzzy or partial. A report that
fits more than one study, or a study that gets more than one report at its best tier, stays unmatched:
nothing is guessed. Unmatched reports are counted and listed by hashed name only.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.deid.types import InputIndex, StudyGroup

MIN_ID_LEN = 3
TIERS = ("accession", "folder", "patient_id")


@dataclass
class ReportMatches:
    by_study: dict[int, Path] = field(default_factory=dict, repr=False)  # index in InputIndex.studies
    tier: dict[int, str] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)  # sha256 of the file name

    @property
    def found(self) -> int:
        return len(self.by_study) + len(self.unmatched)


def hashed_name(path: Path) -> str:
    return hashlib.sha256(path.name.encode()).hexdigest()


def token_in(value: str, text: str) -> bool:
    if len(value) < MIN_ID_LEN:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def _candidates(
    report: Path, studies: list[StudyGroup], by_dir: dict[Path, set[int]]
) -> tuple[str, list[int]]:
    stem = report.stem
    acc = [i for i, g in enumerate(studies) if g.accession and token_in(g.accession, stem)]
    if acc:
        return "accession", acc
    in_dir = by_dir.get(report.parent, set())
    if len(in_dir) == 1:
        return "folder", sorted(in_dir)
    per_patient: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(studies):
        per_patient[g.patient_id].append(i)
    pid = [i for i, g in enumerate(studies) if g.patient_id and token_in(g.patient_id, stem)]
    if pid and all(len(per_patient[studies[i].patient_id]) == 1 for i in pid):
        return "patient_id", pid
    return "", []


def match_reports(index: InputIndex) -> ReportMatches:
    by_dir: dict[Path, set[int]] = defaultdict(set)
    for i, g in enumerate(index.studies):
        for folder in g.folders:
            by_dir[folder].add(i)
    offers: dict[int, list[tuple[int, Path]]] = defaultdict(list)  # study -> (tier rank, report)
    result = ReportMatches()
    for report in index.reports:
        tier, cands = _candidates(report, index.studies, by_dir)
        if len(cands) != 1:  # none, or ambiguous: never guess
            result.unmatched.append(hashed_name(report))
            continue
        offers[cands[0]].append((TIERS.index(tier), report))
    for study, offered in offers.items():
        best = min(rank for rank, _ in offered)
        at_best = [r for rank, r in offered if rank == best]
        if len(at_best) == 1:
            result.by_study[study] = at_best[0]
            result.tier[study] = TIERS[best]
        result.unmatched += [
            hashed_name(r) for r in (r for _, r in offered) if r not in result.by_study.values()
        ]
    return result
