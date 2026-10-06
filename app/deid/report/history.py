"""Layer 5: clinical-history identifiers (SPEC §6.2 layer 5, T19). TR-RPT-04.

- Relatives: ``S/O``, ``W/O``, ``D/O``, ``C/O`` (and "son/wife/daughter/care of") followed by a name.
- Place of residence: ``R/O``, "resident of", "residing at", "village"/"vill." followed by a place.
- Employer: "works at", "employed at/with/by", "employee of" followed by a name.
- Occupation: words from a lexicon, on clinical-history lines and after an "Occupation:" label.
- After a relative, the remaining comma-separated parts of the same clause that are 1-3 Title-case words
  (not clinical terms) are treated as a place ("S/O Ramesh, farmer, Sardhana").
- Other Title-case words left on a history line are counted as ``HISTORY_FLAG`` for the reviewer.
Findings and impression text are not changed (only the relative rule applies outside history lines).
"""

from __future__ import annotations

import re
from collections import Counter

from app.deid.report.clinical_terms import is_clinical

_NAME = r"(?:\[NAME\]|\[REDACTED\]|[A-Z][A-Za-z.']*(?:\s+[A-Z][A-Za-z.']*){0,2})"
# Prefixes match in any case; the name itself must be Title-case (or an earlier redaction tag).
RELATIVE = re.compile(rf"\b((?i:[SWDC]\s*/\s*O|(?:son|wife|daughter|care)\s+of)\.?\s*[:\-]?\s*)({_NAME})")
PLACE = re.compile(rf"\b((?i:R\s*/\s*O|resident\s+of|residing\s+at|village|vill\.)\s*[:\-]?\s*){_NAME}")
EMPLOYER = re.compile(
    rf"\b((?i:works?\s+(?:at|in|for)|working\s+(?:at|in|for)|employed\s+(?:at|with|by)|employee\s+of)\s+)"
    rf"{_NAME}"
)
OCCUPATION_LABEL = re.compile(r"(?im)^(\s*occupation\s*[:\-]\s*)(\S.*)$")
OCCUPATIONS = frozenset(
    "farmer labourer laborer driver teacher shopkeeper student housewife homemaker clerk mason carpenter "
    "tailor weaver fisherman vendor hawker constable soldier engineer nurse peon watchman cook coolie "
    "businessman retired unemployed".split()
)
OCCUPATION = re.compile(r"\b(" + "|".join(sorted(OCCUPATIONS)) + r")\b", re.I)
HISTORY_START = re.compile(
    r"(?i)^\s*(?:clinical\s+(?:history|details|information|indication)|history|hx|indication|"
    r"presenting\s+complaints?|complaints?|c/o\b)"
)
SECTION_END = re.compile(r"(?i)^\s*(?:findings?|impression|opinion|conclusion|technique|observations?)\b")
_TITLE_WORD = re.compile(r"\b[A-Z][a-z][A-Za-z'\-]*\b")


def _places_after_relative(line: str, counts: Counter[str], extra: frozenset[str]) -> str:
    head, sep, tail = line.partition("[RELATIVE]")
    if not sep:
        return line
    clause, rest = re.match(r"([^.;\n]*)(.*)", tail, re.S).groups()  # type: ignore[union-attr]
    parts = clause.split(",")
    for i, part in enumerate(parts[1:], start=1):
        words = part.strip().rstrip(".").split()
        if 1 <= len(words) <= 3 and all(
            _TITLE_WORD.fullmatch(w) and not is_clinical(w, extra) for w in words
        ):
            parts[i] = part.replace(part.strip().rstrip("."), "[PLACE]")
            counts["PLACE"] += 1
    return head + sep + ",".join(parts) + rest


def _history_line(line: str, counts: Counter[str], extra: frozenset[str]) -> str:
    for rx, tag in ((PLACE, "[PLACE]"), (EMPLOYER, "[EMPLOYER]")):
        line, n = rx.subn(rf"\g<1>{re.escape(tag)}", line)
        counts[tag.strip("[]")] += n
    line, n = OCCUPATION.subn("[OCCUPATION]", line)
    counts["OCCUPATION"] += n
    line = _places_after_relative(line, counts, extra)
    body = re.sub(r"^\s*[A-Za-z /]+:\s*", "", line)  # skip the section label itself
    counts["HISTORY_FLAG"] += sum(1 for w in _TITLE_WORD.findall(body) if not is_clinical(w, extra))
    return line


def redact(text: str, counts: Counter[str], extra: frozenset[str] = frozenset()) -> str:
    def rel(m: re.Match[str]) -> str:
        words = re.findall(r"[A-Za-z]+", m.group(2))
        if words and all(is_clinical(w, extra) for w in words):
            return m.group(0)  # "C/O Fever" means complaints of, not care of
        counts["RELATIVE"] += 1
        return m.group(1) + "[RELATIVE]"

    text = RELATIVE.sub(rel, text)
    text, n = OCCUPATION_LABEL.subn(lambda m: m.group(1) + "[OCCUPATION]", text)
    counts["OCCUPATION"] += n
    out, in_history = [], False
    for line in text.split("\n"):
        if HISTORY_START.match(line):
            in_history = True
        elif SECTION_END.match(line) or not line.strip():
            in_history = False
        out.append(_history_line(line, counts, extra) if in_history or "[RELATIVE]" in line else line)
    return "\n".join(out)
