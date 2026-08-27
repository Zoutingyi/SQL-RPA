"""Controlled storage helpers for user contact data."""
import hashlib
import hmac
import re

from config import get_active_encryption_key, get_encryption_keyring, settings
from utils.crypto import decrypt_if_needed, encrypt_if_needed
from utils.masking import mask_phone


def normalize_phone(value: str) -> str:
    return re.sub(r"[ -]", "", value.strip())


def phone_lookup_hash(value: str) -> str:
    normalized = normalize_phone(value)
    return hmac.new(settings.secret_key.encode(), normalized.encode(), hashlib.sha256).hexdigest()


def encrypt_phone(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    normalized = normalize_phone(value)
    version, secret = get_active_encryption_key()
    return encrypt_if_needed(normalized, secret, version), phone_lookup_hash(normalized)


def decrypt_phone(value: str | None) -> str | None:
    if not value:
        return None
    return decrypt_if_needed(value, settings.secret_key, get_encryption_keyring())


def masked_phone(value: str | None) -> str | None:
    plaintext = decrypt_phone(value)
    return mask_phone(plaintext) if plaintext else None
