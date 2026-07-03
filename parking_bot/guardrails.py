"""Guardrail layer.

Two responsibilities:

1. **Input guardrail** (``check_input``) — block obvious prompt-injection and
   attempts to extract confidential/internal data before they reach the agent.
2. **Output guardrail** (``scrub_output``) — redact sensitive data (phone
   numbers, emails, bank/IBAN, gate override codes, card numbers) from generated
   answers so private data stored in the knowledge base cannot leak.

PII detection uses **Microsoft Presidio** (pre-trained spaCy NER) when it and its
model are installed; otherwise it falls back to regular expressions so the module
works — and the test-suite runs — without the large model download.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Regex fallback patterns (also used to augment Presidio for domain entities).
# --------------------------------------------------------------------------- #
_REGEX_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL_ADDRESS": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "PHONE_NUMBER": re.compile(
        r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)"
    ),
    "IBAN_CODE": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    "GATE_CODE": re.compile(r"\b\d{4}-[A-Z]{2,}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
}

# Substrings that indicate a prompt-injection / jailbreak attempt.
_INJECTION_MARKERS = [
    "ignore previous", "ignore all previous", "ignore the above",
    "disregard previous", "disregard the above", "system prompt",
    "you are now", "developer mode", "reveal your instructions",
    "reveal your prompt", "print your system", "act as though",
]

# Substrings that indicate an attempt to extract confidential internal data.
_SENSITIVE_REQUEST_MARKERS = [
    "gate code", "override code", "master code", "settlement account",
    "bank account", "iban", "accountant", "manager's personal",
    "managers personal", "personal phone", "personal mobile",
    "staff phone", "duty officer", "internal contacts",
]


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""


# --------------------------------------------------------------------------- #
# Presidio (optional, lazy)
# --------------------------------------------------------------------------- #
_analyzer = None
_presidio_ready: bool | None = None


def _get_analyzer():
    """Return a Presidio AnalyzerEngine, or None if unavailable."""
    global _analyzer, _presidio_ready
    if _presidio_ready is not None:
        return _analyzer
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        # Use the small spaCy model (light download); fall back handled below.
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
        )
        analyzer = AnalyzerEngine(nlp_engine=provider.create_engine())
        # Domain recognizers Presidio doesn't ship with.
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="GATE_CODE",
                patterns=[Pattern("gate_code", r"\b\d{4}-[A-Z]{2,}\b", 0.8)],
            )
        )
        _analyzer = analyzer
        _presidio_ready = True
    except Exception:
        _analyzer = None
        _presidio_ready = False
    return _analyzer


# --------------------------------------------------------------------------- #
# PII detection / redaction
# --------------------------------------------------------------------------- #
def detect_pii(text: str, entities: list[str] | None = None) -> list[dict]:
    """Detect PII spans. Returns list of {entity_type, start, end, text}."""
    analyzer = _get_analyzer()
    results: list[dict] = []
    if analyzer is not None:
        for r in analyzer.analyze(text=text, language="en", entities=entities):
            results.append(
                {
                    "entity_type": r.entity_type,
                    "start": r.start,
                    "end": r.end,
                    "text": text[r.start : r.end],
                }
            )
    # Always run regex too (covers IBAN/gate-code/phone shapes robustly and
    # provides full coverage when Presidio is unavailable).
    for etype, pattern in _REGEX_PATTERNS.items():
        if entities and etype not in entities:
            continue
        for m in pattern.finditer(text):
            results.append(
                {"entity_type": etype, "start": m.start(), "end": m.end(),
                 "text": m.group()}
            )
    return _dedupe_spans(results)


def _dedupe_spans(spans: list[dict]) -> list[dict]:
    """Drop spans fully contained within another span."""
    spans = sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"])))
    kept: list[dict] = []
    for s in spans:
        if any(k["start"] <= s["start"] and s["end"] <= k["end"] for k in kept):
            continue
        kept.append(s)
    return kept


def redact(text: str, entity_types: list[str] | None = None) -> str:
    """Replace detected PII spans with ``<ENTITY_TYPE>`` placeholders."""
    spans = detect_pii(text, entities=entity_types)
    # Redact right-to-left so indices stay valid.
    for s in sorted(spans, key=lambda x: x["start"], reverse=True):
        text = text[: s["start"]] + f"<{s['entity_type']}>" + text[s["end"] :]
    return text


# --------------------------------------------------------------------------- #
# Public guardrail entry points
# --------------------------------------------------------------------------- #
def check_input(text: str) -> GuardResult:
    """Screen a user message before it reaches the agent."""
    lowered = text.lower()
    for marker in _INJECTION_MARKERS:
        if marker in lowered:
            return GuardResult(
                False,
                "Your message looks like an attempt to change my instructions. "
                "I can only help with parking information and reservations.",
            )
    for marker in _SENSITIVE_REQUEST_MARKERS:
        if marker in lowered:
            return GuardResult(
                False,
                "I can't share internal or confidential staff information. "
                "I can help with parking info, prices, availability, or a booking.",
            )
    return GuardResult(True)


# Entities to strip from generated answers. PERSON/plate are intentionally NOT
# redacted so the bot can confirm the user's own reservation details back to them.
_OUTPUT_REDACT = ["EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN_CODE", "GATE_CODE",
                  "CREDIT_CARD"]


def scrub_output(text: str) -> str:
    """Redact leaked sensitive data from a generated answer."""
    return redact(text, entity_types=_OUTPUT_REDACT)
