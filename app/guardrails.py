"""Input validation, prompt-injection heuristics and output checks.

These are defence-in-depth measures. The main protection is the system prompt, which tells the
model that retrieved text is reference data only; these helpers reduce what reaches the model.
"""
from __future__ import annotations

import re
import unicodedata

# Phrases typical of instructions aimed at an AI rather than at a human reader.
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|rules|messages)",
    r"disregard (all |any |the )?(previous|prior|above|system) (instructions|prompts|rules)",
    r"forget (all |your |the )?(previous |prior )?(instructions|rules|guidelines)",
    r"you are now (a|an|in) ",
    r"(new|updated|override) (system )?(instructions|prompt|rules)\s*:",
    r"\bsystem prompt\b",
    r"\b(reveal|print|show|repeat|output) (your|the) (system |hidden |initial )?(prompt|instructions)",
    r"\bjailbreak\b|\bDAN mode\b|\bdeveloper mode\b",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    r"\bOPENAI_API_KEY\b",
    r"\b(note|message|instruction)s? (to|for) (the )?(ai|assistant|chatbot|llm|language model)\b",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)

_CONTROL_CHARS_RE = re.compile(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F​-‏‪-‮⁠-⁤﻿]")
_CITATION_RE = re.compile(r"\[S(\d{1,2})\]")


def normalise_text(text: str) -> str:
    """NFKC-normalise, strip control/zero-width/bidi characters, collapse whitespace runs."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _CONTROL_CHARS_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


def sanitise_source_text(text: str) -> tuple[str, int]:
    """Remove lines in source material that look like instructions to an AI.

    Returns the cleaned text and the number of lines removed (logged during ingestion).
    """
    kept, removed = [], 0
    for line in normalise_text(text).split("\n"):
        if looks_like_injection(line):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept).strip(), removed


def neutralise_delimiters(text: str) -> str:
    """Stop source text from closing or faking the <source> wrapper used in the prompt."""
    return re.sub(r"<\s*/?\s*source[^>]*>", "", text, flags=re.IGNORECASE)


class ValidationError(ValueError):
    pass


def validate_user_message(message: object, max_chars: int) -> str:
    if not isinstance(message, str):
        raise ValidationError("'message' must be a string.")
    cleaned = normalise_text(message)
    if not cleaned:
        raise ValidationError("Please type a question.")
    if len(cleaned) > max_chars:
        raise ValidationError(f"Please keep questions under {max_chars} characters.")
    return cleaned


def validate_history(history: object, max_turns: int, max_chars: int) -> list[dict]:
    """Accept only well-formed user/assistant turns; truncate long ones; keep the most recent."""
    if history is None:
        return []
    if not isinstance(history, list):
        raise ValidationError("'history' must be a list.")
    clean: list[dict] = []
    for turn in history[-max_turns * 2 :]:
        if not isinstance(turn, dict):
            continue
        role, content = turn.get("role"), turn.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue  # silently drop "system" or malformed turns supplied by a client
        content = normalise_text(content)[: max_chars * 2]
        if content:
            clean.append({"role": role, "content": content})
    return clean


def cited_source_ids(answer: str, valid_ids: set[int]) -> list[int]:
    seen: list[int] = []
    for match in _CITATION_RE.finditer(answer):
        number = int(match.group(1))
        if number in valid_ids and number not in seen:
            seen.append(number)
    return seen


def strip_invalid_citations(answer: str, valid_ids: set[int]) -> str:
    """Remove citation markers that do not correspond to a supplied source (fabricated cites)."""
    return _CITATION_RE.sub(lambda m: m.group(0) if int(m.group(1)) in valid_ids else "", answer)


def strip_all_citations(text: str) -> str:
    """Remove every [S#] marker (used on earlier assistant turns before they are re-sent)."""
    return re.sub(r"\s?\[S\d{1,2}\]", "", text)


SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")


def redact_secrets(text: str) -> str:
    return SECRET_PATTERN.sub("[redacted]", text)
