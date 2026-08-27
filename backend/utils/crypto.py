"""Versioned AES-GCM encryption helpers."""

import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


def _get_key(secret: str) -> bytes:
    """Derive 32-byte AES key from secret string."""
    return hashlib.sha256(secret.encode()).digest()


def encrypt(plaintext: str, secret: str) -> str:
    """AES-256-GCM encrypt, return base64-encoded ciphertext."""
    key = _get_key(secret)
    iv = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext.encode("utf-8")) + encryptor.finalize()
    return base64.b64encode(iv + encryptor.tag + ciphertext).decode("ascii")


def decrypt(encoded: str, secret: str) -> str:
    """Decrypt base64-encoded AES-256-GCM ciphertext."""
    key = _get_key(secret)
    raw = base64.b64decode(encoded)
    iv, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend())
    decryptor = cipher.decryptor()
    return (decryptor.update(ciphertext) + decryptor.finalize()).decode("utf-8")


def encrypt_if_needed(value: str, secret: str, version: str = "1") -> str:
    """Encrypt value if it's a plaintext API key (not already encrypted)."""
    if not value or value.startswith("ENC:"):
        return value
    return f"ENC:v{version}:{encrypt(value, secret)}"


def decrypt_if_needed(value: str, secret: str, keyring: dict[str, str] | None = None) -> str:
    """Decrypt an envelope using its key version (or the legacy key)."""
    if value and value.startswith("ENC:v"):
        try:
            marker, encoded = value[4:].split(":", 1)
        except ValueError as exc:
            raise ValueError("Invalid encrypted value envelope") from exc
        version = marker.removeprefix("v")
        selected = (keyring or {}).get(version) or (secret if version == "1" else None)
        if not selected:
            raise ValueError(f"Encryption key version is unavailable: {version}")
        return decrypt(encoded, selected)
    if value and value.startswith("ENC:"):
        return decrypt(value[4:], secret)
    return value


def reencrypt(value: str, legacy_secret: str, active_version: str,
              keyring: dict[str, str]) -> str:
    """Re-encrypt a supported value with the active key version."""
    active_secret = keyring.get(active_version)
    if not active_secret:
        raise ValueError(f"Active encryption key is unavailable: {active_version}")
    return encrypt_if_needed(
        decrypt_if_needed(value, legacy_secret, keyring),
        active_secret,
        active_version,
    )
