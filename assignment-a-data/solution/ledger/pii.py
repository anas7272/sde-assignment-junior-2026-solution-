"""PII protection.

* **Phone (matching):** HMAC-SHA256 with a secret key ("pepper"), never a bare
  hash: a 10-digit mobile space is only ~4e9 values, so an unkeyed hash can be
  reversed in minutes. The key lives outside the database.
* **Other PII fields:** authenticated encryption (encrypt-then-MAC). This is a
  stdlib-only construction (HMAC-SHA256 keystream in counter mode + HMAC tag)
  so the project has zero dependencies; in production swap it for AES-GCM /
  envelope encryption with a KMS. The interface (`encrypt`/`decrypt`) stays.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

ENV_KEY = "LEDGER_PII_KEY"


def _kdf(master: bytes, label: bytes) -> bytes:
    return hmac.new(master, b"ledger/" + label, hashlib.sha256).digest()


class PiiVault:
    def __init__(self, master: bytes):
        self._hash_key = _kdf(master, b"phone-hash")
        self._enc_key = _kdf(master, b"field-enc")
        self._mac_key = _kdf(master, b"field-mac")

    def phone_hash(self, canonical_phone: str) -> str:
        return hmac.new(self._hash_key, canonical_phone.encode(), hashlib.sha256).hexdigest()

    def _keystream(self, nonce: bytes, n: int) -> bytes:
        blocks = (n + 31) // 32
        return b"".join(
            hmac.new(self._enc_key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
            for i in range(blocks))[:n]

    def encrypt(self, plaintext: str) -> str:
        data = plaintext.encode("utf-8")
        nonce = os.urandom(16)
        ks = self._keystream(nonce, len(data))
        ct = (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big") if data else b""
        tag = hmac.new(self._mac_key, nonce + ct, hashlib.sha256).digest()
        return "v1." + base64.urlsafe_b64encode(nonce + ct + tag).decode()

    def decrypt(self, token: str) -> str:
        if not token.startswith("v1."):
            raise ValueError("unknown ciphertext version")
        blob = base64.urlsafe_b64decode(token[3:])
        nonce, ct, tag = blob[:16], blob[16:-32], blob[-32:]
        expect = hmac.new(self._mac_key, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expect):
            raise ValueError("ciphertext failed authentication (wrong key or tampered)")
        ks = self._keystream(nonce, len(ct))
        data = (int.from_bytes(ct, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(ct), "big") if ct else b""
        return data.decode("utf-8")


def load_vault(db_path: str) -> PiiVault:
    """Key from $LEDGER_PII_KEY, else a 0600 key file next to the DB (created once)."""
    env = os.environ.get(ENV_KEY)
    if env:
        return PiiVault(hashlib.sha256(env.encode()).digest())
    key_path = Path(str(db_path) + ".key")
    if key_path.exists():
        return PiiVault(key_path.read_bytes())
    key = os.urandom(32)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return PiiVault(key)
