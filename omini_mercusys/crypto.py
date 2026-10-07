"""The encryption of the local web interface (shared with TP-Link Deco).

- The password is encrypted with the unit's RSA key (PKCS#1 v1.5, split in
  blocks, hex-joined).
- Every request after that is AES-128-CBC encrypted with a key and IV the
  client picks (16-digit numbers), and signed with the unit's other RSA key:
  ``h=<md5(user+password)>&s=<seq + data length>``, plus ``k=<key>&i=<iv>``
  in front on the login, which is how the unit learns the AES key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import quote_plus

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PKCS1_HEADER = 11


def rsa_encrypt(n: int, e: int, plaintext: bytes) -> str:
    key = RSAPublicNumbers(e, n).public_key()
    size = (n.bit_length() + 7) // 8
    step = size - PKCS1_HEADER
    return "".join(
        key.encrypt(plaintext[i : i + step], rsa_padding.PKCS1v15()).hex()
        for i in range(0, len(plaintext), step)
    )


def new_aes() -> tuple[str, str]:
    """Key and IV: 16-digit numbers without a leading zero, as the units expect."""
    low, high = 10**15, 10**16 - 1
    return str(secrets.randbelow(high - low) + low), str(secrets.randbelow(high - low) + low)


def aes_encrypt(key: str, iv: str, plaintext: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext) + padder.finalize()
    enc = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode())).encryptor()
    return enc.update(data) + enc.finalize()


def aes_decrypt(key: str, iv: str, ciphertext: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode())).decryptor()
    data = dec.update(ciphertext) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(data) + unpadder.finalize()


class Session:
    """What a signed-in client needs; saved between collections."""

    def __init__(self, username: str, password: str, values: dict[str, Any] | None = None):
        self.username = username
        self.password = password
        v = values or {}
        self.key: str = v.get("key") or ""
        self.iv: str = v.get("iv") or ""
        if not self.key:
            self.key, self.iv = new_aes()
        self.sign_n: int = int(v.get("sign_n") or 0)
        self.sign_e: int = int(v.get("sign_e") or 0)
        self.seq: int = int(v.get("seq") or 0)
        self.stok: str = v.get("stok") or ""
        self.cookie: str = v.get("cookie") or ""

    def values(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "iv": self.iv,
            "sign_n": str(self.sign_n),
            "sign_e": str(self.sign_e),
            "seq": self.seq,
            "stok": self.stok,
            "cookie": self.cookie,
        }

    def encode(self, payload: dict[str, Any], login: bool = False) -> str:
        """The body of a signed, encrypted request. The login's signature also
        carries the AES key and IV; later requests sign the hash and length."""
        data = base64.b64encode(
            aes_encrypt(self.key, self.iv, json.dumps(payload, separators=(",", ":")).encode())
        ).decode()
        auth = hashlib.md5(f"{self.username}{self.password}".encode()).hexdigest()
        text = f"h={auth}&s={self.seq + len(data)}"
        if login:
            text = f"k={self.key}&i={self.iv}&{text}"
        sign = rsa_encrypt(self.sign_n, self.sign_e, text.encode())
        return f"sign={sign}&data={quote_plus(data)}"

    def decode(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            return data
        if not data:
            return {}
        return json.loads(aes_decrypt(self.key, self.iv, base64.b64decode(data)))
