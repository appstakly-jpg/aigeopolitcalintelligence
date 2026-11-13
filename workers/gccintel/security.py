"""Password hashing (scrypt) and secret encryption (AES-256-GCM).

Formats are shared with the Next.js app (apps/web/src/lib/crypto.ts):
* password: "scrypt$16384$8$1$<salt b64>$<key b64>"  (64-byte key)
* secret:   "v1:<base64(nonce12 || ciphertext || tag16)>", AAD = "<tenant_id>:<name>"
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN = 16384, 8, 1, 64


def hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    salt = salt or os.urandom(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)
    return "$".join(["scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
                     base64.b64encode(salt).decode(), base64.b64encode(key).decode()])


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, key_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(key_b64)
        key = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
                             dklen=len(expected))
        return hmac.compare_digest(key, expected)
    except (ValueError, TypeError):
        return False


def _key(raw: str | None) -> bytes:
    if not raw:
        raise RuntimeError("APP_ENCRYPTION_KEY is not set; cannot encrypt or decrypt tenant secrets")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError("APP_ENCRYPTION_KEY must be 32 random bytes, base64-encoded")
    return key


def encrypt_secret(plaintext: str, *, tenant_id: str, name: str, key_b64: str | None) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key(key_b64)).encrypt(nonce, plaintext.encode("utf-8"), f"{tenant_id}:{name}".encode())
    return "v1:" + base64.b64encode(nonce + ct).decode()


def decrypt_secret(token: str, *, tenant_id: str, name: str, key_b64: str | None) -> str:
    if not token.startswith("v1:"):
        raise ValueError("unknown secret format")
    raw = base64.b64decode(token[3:])
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_key(key_b64)).decrypt(nonce, ct, f"{tenant_id}:{name}".encode()).decode("utf-8")


def mask(value: str | None, keep: int = 3) -> str:
    if not value:
        return ""
    if "@" in value:
        local, _, domain = value.partition("@")
        return (local[:keep] + "***@" + domain) if local else "***@" + domain
    return value[:keep] + "***" if len(value) > keep else "***"
