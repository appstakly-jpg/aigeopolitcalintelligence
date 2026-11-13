"""Deterministic language identification for the languages the platform supports first
(Arabic, English) plus configurable additions (Persian, Hebrew, Turkish, French).

Script ratios decide the family; Persian is distinguished from Arabic by letters that
do not exist in Arabic and by high-frequency function words. Returns (code, confidence).
"""

from __future__ import annotations

import re

_PERSIAN_ONLY = set("پچژگکی")  # letters absent from standard Arabic orthography
_PERSIAN_WORDS = {"است", "این", "که", "را", "می", "های", "برای", "با", "از", "شد", "شده", "کرد", "ایران", "نیز"}
_ARABIC_WORDS = {"في", "من", "على", "إلى", "الى", "أن", "ان", "التي", "الذي", "عن", "مع", "هذا", "هذه", "قال", "وقال"}
_EN_WORDS = {"the", "and", "of", "to", "in", "on", "for", "with", "said", "was", "is", "by", "at", "from", "a"}
_FR_WORDS = {"le", "la", "les", "des", "et", "en", "du", "une", "un", "dans", "pour", "est", "sur", "au"}
_TR_CHARS = set("ğışçöüĞİŞÇÖÜ")
_TR_WORDS = {"ve", "bir", "bu", "için", "ile", "da", "de", "olarak", "sonra"}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text: str | None, hint: str | None = None) -> tuple[str, float]:
    if not text or not text.strip():
        return (hint or "und", 0.0)
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return (hint or "und", 0.0)
    total = len(letters)
    arabic_script = sum(1 for ch in letters if "؀" <= ch <= "ۿ" or "ݐ" <= ch <= "ݿ" or "ﭐ" <= ch <= "﷿")
    hebrew = sum(1 for ch in letters if "֐" <= ch <= "׿")
    latin = sum(1 for ch in letters if ch.isascii() or "À" <= ch <= "ɏ")

    words = [w.lower() for w in _WORD_RE.findall(text)]
    wordset = set(words)

    if arabic_script / total >= 0.5:
        persian_letters = sum(1 for ch in letters if ch in _PERSIAN_ONLY)
        fa_hits = len(wordset & _PERSIAN_WORDS)
        ar_hits = len(wordset & _ARABIC_WORDS)
        if persian_letters / max(arabic_script, 1) > 0.02 and fa_hits >= ar_hits:
            return ("fa", min(0.99, 0.6 + 0.05 * fa_hits))
        return ("ar", min(0.99, 0.75 + 0.03 * ar_hits + 0.2 * arabic_script / total))
    if hebrew / total >= 0.5:
        return ("he", 0.9)
    if latin / total >= 0.5:
        en = len([w for w in words if w in _EN_WORDS])
        fr = len([w for w in words if w in _FR_WORDS])
        tr = len([w for w in words if w in _TR_WORDS]) + 2 * sum(1 for ch in letters if ch in _TR_CHARS)
        best = max((en, "en"), (fr, "fr"), (tr, "tr"))
        if best[0] == 0:
            # Short headline with no function words: trust the source hint if Latin-script.
            if hint in {"en", "fr", "tr"}:
                return (hint, 0.5)
            return ("en", 0.4)
        conf = best[0] / max(1, en + fr + tr)
        return (best[1], round(min(0.99, 0.5 + 0.5 * conf), 2))
    return (hint or "und", 0.2)
