"""Versioned client wire contract. This module never derives or stores keys."""
import base64
import binascii
import json
from entity.models import EncryptionConfiguration

PURPOSES = EncryptionConfiguration.PURPOSE_CHOICES
KDF_PARAMETERS = {"version": 19, "memory_kib": 65536, "iterations": 3, "parallelism": 1, "output_length": 32}
SUPPORTED_PROFILE = {
    "format_version": 1, "kdf_algorithm": "argon2id", "kdf_parameters": KDF_PARAMETERS,
    "cipher_algorithm": "aes-256-gcm", "salt_bytes": 16, "nonce_bytes": 12,
    "tag_bytes": 16, "verification_token_bytes": 32,
    "verification_marker": "chatterloop.encryption.verification.v1",
}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def decode_base64(value, minimum, maximum):
    if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
        raise ValueError("Invalid binary envelope.")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid binary envelope.") from None
    if not minimum <= len(decoded) <= maximum or base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("Invalid binary envelope.")
    return decoded


def verification_aad(configuration):
    return canonical_json([
        "chatterloop.encryption.verification", configuration.format_version,
        str(configuration.id), str(configuration.entity_id), str(configuration.user_id), configuration.purpose,
    ])
