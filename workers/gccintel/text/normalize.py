"""Text normalisation for English and Arabic (Persian-aware).

Two levels:
* `clean_text` - safe display text: HTML stripped, entities decoded, whitespace collapsed.
* `match_form` - aggressive form used only for matching/dedup: lower-cased, Arabic
  diacritics/tatweel removed, alef/ya/ta-marbuta unified, punctuation removed.
"""

from __future__ import annotations

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile("[​‌‍⁠﻿‪-‮⁦-⁩]")
_AR_DIACRITICS_RE = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_POSSESSIVE_RE = re.compile(r"(?<=\w)['’]s\b")

_AR_CHAR_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
        "ک": "ك",  # Persian kaf -> Arabic kaf (for matching only)
        "ی": "ي",  # Persian ya -> Arabic ya
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
    }
)

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?؟。])\s+|\n+")


def strip_html(text: str) -> str:
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return html.unescape(text)


_MOJIBAKE_RE = re.compile("[ØÙÚÛ][\u0080-\u00ff\u0152-\u2122]|Ã[\u0080-\u00bf]|â€")


def repair_mojibake(text: str) -> str:
    """Undo UTF-8 text that was decoded as Windows-1252 somewhere upstream ("Ø£Ø·ÙØ§Ù„" -> "أطفال"),
    seen in live Al Jazeera Arabic video titles. Only applied when the whole text re-decodes cleanly."""
    if not text or not _MOJIBAKE_RE.search(text):
        return text
    raw = bytearray()
    try:
        for ch in text:
            o = ord(ch)
            raw.extend(bytes([o]) if o <= 0xFF else ch.encode("cp1252"))
        fixed = raw.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return fixed


def clean_text(text: str | None, max_chars: int | None = None) -> str:
    """Display-safe text: no HTML, no zero-width/bidi control characters, collapsed spaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", repair_mojibake(strip_html(text)))
    text = _ZERO_WIDTH_RE.sub("", text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = _WS_RE.sub(" ", text).strip()
    if max_chars is not None and len(text) > max_chars:
        cut = text[:max_chars]
        # do not cut mid-word when possible
        if " " in cut[int(max_chars * 0.6):]:
            cut = cut[: cut.rfind(" ")]
        text = cut.rstrip(" ,;:-") + "…"
    return text


def normalize_arabic(text: str) -> str:
    text = _AR_DIACRITICS_RE.sub("", text)
    text = text.replace(_TATWEEL, "")
    return text.translate(_AR_CHAR_MAP)


def match_form(text: str | None) -> str:
    """Normalised form for keyword / gazetteer matching and near-duplicate hashing."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _ZERO_WIDTH_RE.sub("", text)
    text = normalize_arabic(text)
    # "UAE's" / "Yemen’s" -> "UAE" / "Yemen": a possessive must not hide a place or actor name.
    text = _POSSESSIVE_RE.sub("", text)
    text = text.replace("'", "").replace("’", "").replace("`", "")
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def tokens(text: str | None) -> list[str]:
    return match_form(text).split()


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text or "") if p and p.strip()]
    return parts


# Arabic clitics that commonly prefix nouns (و, ف, ب, ل, ك, ال combinations).
_AR_PREFIXES = ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ف", "ب", "ل")


def arabic_variants(token: str) -> set[str]:
    """Token plus forms with common proclitics stripped (for matching only)."""
    out = {token}
    for prefix in _AR_PREFIXES:
        if token.startswith(prefix) and len(token) - len(prefix) >= 2:
            out.add(token[len(prefix):])
    return out


def contains_phrase(haystack_tokens: list[str], phrase: str) -> bool:
    """Token-boundary phrase match on normalised tokens, tolerant of Arabic proclitics
    on the first token (e.g. "والحوثيون" matches "الحوثيون")."""
    needle = match_form(phrase).split()
    if not needle:
        return False
    n = len(needle)
    first = needle[0]
    is_ar = any("؀" <= ch <= "ۿ" for ch in first)
    for i in range(len(haystack_tokens) - n + 1):
        tok = haystack_tokens[i]
        if tok == first or (is_ar and first in arabic_variants(tok)) or (is_ar and ("ال" + first) in arabic_variants(tok)):
            if haystack_tokens[i + 1 : i + n] == needle[1:]:
                return True
    return False
