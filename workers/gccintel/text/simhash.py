"""64-bit SimHash over word shingles for near-duplicate (syndication) detection.

Works on `match_form` text so Arabic orthographic variants and punctuation differences
do not hide copies. Hamming distance <= 6 on headline+excerpt is treated as a near copy.
"""

from __future__ import annotations

import hashlib

from .normalize import match_form

MASK64 = (1 << 64) - 1


def _hash64(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


def shingles(text: str, size: int = 3) -> list[str]:
    words = match_form(text).split()
    if len(words) < size:
        return [" ".join(words)] if words else []
    return [" ".join(words[i : i + size]) for i in range(len(words) - size + 1)]


def simhash(text: str) -> int:
    weights = [0] * 64
    feats = shingles(text)
    if not feats:
        return 0
    for feat in feats:
        h = _hash64(feat)
        for bit in range(64):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    value = 0
    for bit in range(64):
        if weights[bit] > 0:
            value |= 1 << bit
    return value


def to_signed(value: int) -> int:
    """Postgres bigint is signed; store the 64-bit pattern as a signed integer."""
    value &= MASK64
    return value - (1 << 64) if value >= (1 << 63) else value


def from_signed(value: int) -> int:
    return value & MASK64


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & MASK64).count("1")


def jaccard(a: str, b: str) -> float:
    sa, sb = set(shingles(a, 2)), set(shingles(b, 2))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
