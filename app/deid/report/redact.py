"""Report redaction, layers 1-3, and the full layer order (SPEC §6.2). TR-RPT-02, TR-RPT-04.

Port of ``reference/deid_prototype/core.LABEL_LINE`` / ``REGEX_RULES`` / ``redact_report`` with:
- header identifiers matched on token boundaries (layer 2, vendor exception applied when collected);
- the title of a titled name matched in any case (``DR. ANIL``) and with no space (``Dr.Kavita``).
Counts are per rule name; the redacted text itself is never logged.
"""

from __future__ import annotations

import re
from collections import Counter

from app.deid.identifiers import identifier_regex

REDACTED = "[REDACTED]"

LABEL_LINE = re.compile(
    r"(?im)^(\s*(?:patient\s*name|pt\.?\s*name|name|uhid|mrn|reg(?:istration)?\.?\s*no\.?|patient\s*id|"
    r"ip\s*no\.?|op\s*no\.?|lab\s*no\.?|accession(?:\s*no\.?)?|ref(?:erred)?\.?\s*by|"
    r"referring\s*(?:doctor|physician)|address|mobile|phone|contact(?:\s*no\.?)?|e-?mail|aadhaa?r|"
    r"abha(?:\s*(?:id|no\.?))?|d\.?o\.?b\.?|date\s*of\s*birth)"
    r"\s*[:\-]\s*)(\S.*)$"
)
_TITLE = r"(?i:Dr|Mr|Mrs|Ms|Miss|Smt|Shri|Sri|Kumari|Master|Baby)"
REGEX_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("ID12", re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("ABHA", re.compile(r"(?<!\d)\d{2}-\d{4}-\d{4}-\d{4}(?!\d)")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    (
        "REGNO",
        re.compile(
            r"\b(?:Reg(?:istration)?|IP|OP|Lab)\.?\s*(?:No\.?|Number)\s*[:#-]?\s*(?=[A-Z0-9/\-]*\d)"
            r"[A-Z0-9][A-Z0-9/\-]{3,}",
            re.I,
        ),
    ),
    (
        "MRN",
        re.compile(r"\b(?:UHID|MRN|ABHA)\s*(?:No\.?|ID)?\s*[:#-]?\s*(?=\S*\d)[A-Z0-9][A-Z0-9/\-]{3,}", re.I),
    ),
    (
        "DATE",
        re.compile(
            r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|"
            r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+\d{2,4}\b",
            re.I,
        ),
    ),
    ("PIN", re.compile(r"(?<!\d)[1-9]\d{5}(?!\d)")),
    (
        "NAME",
        # titled name on one line: "Dr. Anil Deshpande", "Dr.Kavita", "DR. ANIL DESHPANDE"
        re.compile(
            rf"\b{_TITLE}(?:\.[ \t]*|[ \t]+)[A-Z][a-zA-Z]+(?:[ \t]+[A-Z]\.)?(?:[ \t]+[A-Z][a-zA-Z]+){{0,2}}"
        ),
    ),
]
RESIDUAL = frozenset({"EMAIL", "PHONE", "ID12", "PAN", "ABHA"})  # A4 residual scan


def redact_labelled(text: str, counts: Counter[str]) -> str:
    def sub(m: re.Match[str]) -> str:
        counts["LABELLED_FIELD"] += 1
        return m.group(1) + REDACTED

    return LABEL_LINE.sub(sub, text)


def redact_known(text: str, identifiers: frozenset[str], counts: Counter[str]) -> str:
    rx = identifier_regex(identifiers)
    if rx is None:
        return text
    text, n = rx.subn(REDACTED, text)
    counts["KNOWN_ID"] += n
    return text


def redact_patterns(text: str, counts: Counter[str]) -> str:
    for name, rx in REGEX_RULES:
        text, n = rx.subn(f"[{name}]", text)
        counts[name] += n
    return text


def residual_patterns(text: str) -> list[str]:
    return [name for name, rx in REGEX_RULES if name in RESIDUAL and rx.search(text)]


def redact_report(text: str, identifiers: frozenset[str], use_ner: bool) -> tuple[str, dict[str, int]]:
    """All layers in SPEC order: labelled fields, header IDs, Indian patterns, NER, clinical history."""
    from app.deid.report import history, ner

    counts: Counter[str] = Counter()
    text = redact_labelled(text, counts)
    text = redact_known(text, identifiers, counts)
    text = redact_patterns(text, counts)
    if use_ner:
        text = ner.redact(text, counts)
    text = history.redact(text, counts)
    return text, {k: v for k, v in sorted(counts.items()) if v}
