"""Layer 4: names and places by NER (SPEC §6.2). TR-RPT-03. Port of ``reference/deid_prototype/text_ner``.

Presidio + spaCy ``en_core_web_sm``, PERSON and LOCATION with score ≥ 0.6. The small model over-calls
clinical phrases as names, so a span is redacted only if every word is Title-case and none is a clinical
term. The registry holds ONLY the spaCy recognizer: Presidio's e-mail/URL recognizers use tldextract, which
can fetch the public suffix list over the network, and the app makes no outbound calls (CLAUDE.md rule 15).
The model is never downloaded at runtime: if it is not installed, NER is reported unavailable.
"""

from __future__ import annotations

import importlib.util
import re
from collections import Counter
from typing import Any

from app.deid.report.clinical_terms import is_clinical

MODEL = "en_core_web_sm"
MIN_SCORE = 0.6
ENTITIES = ["PERSON", "LOCATION"]
_engine: Any = None


def available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("presidio_analyzer", "spacy", MODEL))


def _load() -> Any:
    global _engine
    if _engine is None:
        if not available():
            raise RuntimeError("NER model not installed")
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_analyzer.predefined_recognizers import SpacyRecognizer

        nlp = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": MODEL}],
            }
        ).create_engine()
        registry = RecognizerRegistry(supported_languages=["en"])
        registry.add_recognizer(SpacyRecognizer(supported_language="en", supported_entities=ENTITIES))
        _engine = AnalyzerEngine(registry=registry, nlp_engine=nlp, supported_languages=["en"])
    return _engine


def plausible(span: str, extra: frozenset[str] = frozenset()) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", span)
    if not words:
        return False
    return all(re.fullmatch(r"[A-Z][a-z][a-zA-Z'\-]*", w) and not is_clinical(w, extra) for w in words)


def redact(text: str, counts: Counter[str], extra: frozenset[str] = frozenset()) -> str:
    results = _load().analyze(text=text, language="en", entities=ENTITIES)
    for r in sorted(results, key=lambda r: r.start, reverse=True):
        span = text[r.start : r.end]
        if r.score < MIN_SCORE or "[" in span or not plausible(span, extra):
            continue
        tag = "[NAME]" if r.entity_type == "PERSON" else "[LOCATION]"
        text = text[: r.start] + tag + text[r.end :]
        counts["NER_" + r.entity_type] += 1
    return text
