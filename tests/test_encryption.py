"""Tests for the encryption module — PBKDF2 key derivation and AES-256-GCM encrypt/decrypt."""

from __future__ import annotations

import struct

from src.encryption import _b64_decode_no_pad, _b64_encode_no_pad, decrypt, derive_key, encrypt


class TestDeriveKey:
    def test_key_length(self):
        key = derive_key("TESTKEY", "example_salt", 10_000)
        assert len(key) == 32  # AES-256 requires 32 bytes

    def test_deterministic(self):
        """Same inputs should always produce the same key."""
        key1 = derive_key("TESTKEY", "example_salt", 10_000)
        key2 = derive_key("TESTKEY", "example_salt", 10_000)
        assert key1 == key2

    def test_different_keys(self):
        """Different security keys produce different encryption keys."""
        key1 = derive_key("TESTKEY", "example_salt", 10_000)
        key2 = derive_key("OTHKEY1", "example_salt", 10_000)
        assert key1 != key2

    def test_different_salts(self):
        """Different salts produce different encryption keys."""
        key1 = derive_key("TESTKEY", "example_salt", 10_000)
        key2 = derive_key("TESTKEY", "other_salt", 10_000)
        assert key1 != key2

    def test_key_is_bytes(self):
        key = derive_key("TESTKEY")
        assert isinstance(key, bytes)


class TestBase64NoPad:
    def test_roundtrip(self):
        data = b"Hello, World!"
        encoded = _b64_encode_no_pad(data)
        assert "=" not in encoded  # no padding
        decoded = _b64_decode_no_pad(encoded)
        assert decoded == data

    def test_various_lengths(self):
        """Test that padding-free base64 works for various data lengths."""
        for length in range(1, 50):
            data = bytes(range(length))
            encoded = _b64_encode_no_pad(data)
            assert "=" not in encoded
            decoded = _b64_decode_no_pad(encoded)
            assert decoded == data

    def test_12_bytes_gives_16_chars(self):
        """12-byte nonce should produce exactly 16 base64 characters."""
        nonce = b"\x00" * 12
        encoded = _b64_encode_no_pad(nonce)
        assert len(encoded) == 16


class TestEncryptDecrypt:
    def test_roundtrip(self):
        key = derive_key("TESTKEY")
        plaintext = "Hello, SpoolEase!"
        encrypted = encrypt(key, plaintext)
        decrypted = decrypt(key, encrypted)
        assert decrypted == plaintext

    def test_roundtrip_json(self):
        """Test with JSON data (typical API payload)."""
        key = derive_key("TESTKEY")
        plaintext = '{"test":"Hello","value":42}'
        encrypted = encrypt(key, plaintext)
        decrypted = decrypt(key, encrypted)
        assert decrypted == plaintext

    def test_roundtrip_empty_string(self):
        key = derive_key("TESTKEY")
        encrypted = encrypt(key, "")
        decrypted = decrypt(key, encrypted)
        assert decrypted == ""

    def test_roundtrip_unicode(self):
        key = derive_key("TESTKEY")
        plaintext = "PLA filament — 1.75mm"
        encrypted = encrypt(key, plaintext)
        decrypted = decrypt(key, encrypted)
        assert decrypted == plaintext

    def test_roundtrip_csv_data(self):
        """Test with CSV data like SpoolEase returns."""
        key = derive_key("TESTKEY")
        csv = "1,04A3B2C1D5E6F7,PLA,,Black,000000FF,,Bambu,1000,200,,,,,,,,,n,,SpoolEaseV1"
        encrypted = encrypt(key, csv)
        decrypted = decrypt(key, encrypted)
        assert decrypted == csv

    def test_different_encryptions_differ(self):
        """Same plaintext should produce different ciphertext (random nonce)."""
        key = derive_key("TESTKEY")
        enc1 = encrypt(key, "test")
        enc2 = encrypt(key, "test")
        assert enc1 != enc2  # different nonces

    def test_encrypted_format(self):
        """Verify the encrypted output format: 16-char nonce + ciphertext."""
        key = derive_key("TESTKEY")
        encrypted = encrypt(key, "test")
        # First 16 chars should be valid base64 (the nonce)
        nonce_b64 = encrypted[:16]
        nonce = _b64_decode_no_pad(nonce_b64)
        assert len(nonce) == 12  # 12-byte nonce
        # Rest should also be valid base64
        ct_b64 = encrypted[16:]
        ct = _b64_decode_no_pad(ct_b64)
        # Ciphertext should be plaintext length + 16 bytes (GCM tag)
        assert len(ct) == len("test".encode()) + 16

    def test_wrong_key_fails(self):
        """Decryption with wrong key should raise an error."""
        key1 = derive_key("TESTKEY")
        key2 = derive_key("WRONGKY")
        encrypted = encrypt(key1, "secret data")
        try:
            decrypt(key2, encrypted)
            assert False, "Should have raised an exception"
        except Exception:
            pass  # Expected
