"""AES-256-GCM encryption/decryption matching SpoolEase's framework.

Replicates the encrypt/decrypt scheme from esp-hal-app-framework v0.6.1.
Key derivation uses PBKDF2-HMAC-SHA256 with known parameters from SpoolEase settings.rs.

Encrypted format: base64_no_pad(12-byte nonce) + base64_no_pad(ciphertext + 16-byte auth tag)
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def derive_key(security_key: str, salt: str = "example_salt", iterations: int = 10_000) -> bytes:
    """Derive a 32-byte AES key from the security key using PBKDF2-HMAC-SHA256.

    Matches the framework's derive_encryption_key() and the WASM derive_key() function.
    """
    return hashlib.pbkdf2_hmac(
        "sha256",
        security_key.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
        dklen=32,
    )


def _b64_encode_no_pad(data: bytes) -> str:
    """Base64 encode without padding, matching Rust's STANDARD_NO_PAD."""
    return base64.b64encode(data).rstrip(b"=").decode("ascii")


def _b64_decode_no_pad(s: str) -> bytes:
    """Base64 decode without padding, adding padding back as needed."""
    padded = s + "=" * (-len(s) % 4)
    return base64.b64decode(padded)


def encrypt(key: bytes, plaintext: str) -> str:
    """Encrypt a string using AES-256-GCM.

    Returns: base64_no_pad(nonce) + base64_no_pad(ciphertext_with_tag)
    The nonce is 12 bytes (16 base64 chars without padding).
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return _b64_encode_no_pad(nonce) + _b64_encode_no_pad(ciphertext_with_tag)


def decrypt(key: bytes, encrypted: str) -> str:
    """Decrypt an AES-256-GCM encrypted string.

    The first 16 characters are the base64-no-pad encoded 12-byte nonce.
    The rest is the base64-no-pad encoded ciphertext + auth tag.
    """
    nonce = _b64_decode_no_pad(encrypted[:16])
    ciphertext_with_tag = _b64_decode_no_pad(encrypted[16:])
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    return plaintext.decode("utf-8")
