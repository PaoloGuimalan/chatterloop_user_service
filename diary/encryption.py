"""Validate opaque Diary field envelopes without accepting decryption keys."""
import json
import uuid

from rest_framework.exceptions import ValidationError

from entity.encryption_contract import CONSUMER_CAPABILITIES, decode_base64
from entity.models import EncryptionConfiguration, EncryptionPurpose


_FIELDS = {"v", "configuration_id", "entry_binding", "nonce", "ciphertext"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate envelope field.")
        result[key] = value
    return result


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid envelope identity.")
    return value


def _envelope(value, maximum):
    if not isinstance(value, str) or len(value) > maximum * 2 + 512:
        raise ValueError("Invalid envelope size.")
    obj = json.loads(value, object_pairs_hook=_unique_object)
    if not isinstance(obj, dict) or set(obj) != _FIELDS or type(obj["v"]) is not int or obj["v"] != CONSUMER_CAPABILITIES["diary"]["format_version"]:
        raise ValueError("Unsupported envelope.")
    _uuid(obj["configuration_id"])
    _uuid(obj["entry_binding"])
    decode_base64(obj["nonce"], 12, 12)
    decode_base64(obj["ciphertext"], 16, maximum)
    return obj


def _looks_encrypted(value):
    try:
        obj = json.loads(value)
        return isinstance(obj, dict) and bool({"configuration_id", "ciphertext"} & set(obj))
    except (TypeError, ValueError):
        return False


def validate_entry_encryption(account, title, content, is_encrypted, is_private, instance=None, *, explicit_choice=False):
    """Call inside the Account write lock before storing a new entry.

    Ciphertext stays in the two existing text fields. Both field envelopes
    reference the same configuration and random entry binding; their distinct
    nonces and field-specific authenticated data are verified by the clients.
    """
    if type(is_encrypted) is not bool:
        raise ValidationError({"is_encrypted": "Use a boolean encryption marker."})
    if not isinstance(title, str) or not isinstance(content, str):
        raise ValidationError({"message": "Title and content must be text."})
    historical = instance is not None and instance.is_encrypted
    if not is_encrypted:
        if historical:
            raise ValidationError({"message": "An encrypted entry cannot be saved as plaintext."})
        if _looks_encrypted(title) or _looks_encrypted(content):
            raise ValidationError({"message": "Encrypted fields require the encryption marker."})
        if len(title) > 255:
            raise ValidationError({"title": "Use at most 255 characters."})
        if instance is None and not explicit_choice and EncryptionConfiguration.objects.filter(
            user=account, entity_id=account.entity_id, purposes__code="diary", purposes__is_active=True, is_active=True,
        ).exists():
            raise ValidationError({"code": "diary_encryption_required", "message": "Choose whether to encrypt this entry. Update your client before saving."})
        return
    if is_private is not True:
        raise ValidationError({"message": "Encrypted Diary entries must be private."})
    try:
        title_envelope = _envelope(title, 16 * 1024)
        content_envelope = _envelope(content, 2 * 1024 * 1024)
        if any(title_envelope[key] != content_envelope[key] for key in ["configuration_id", "entry_binding"]):
            raise ValueError("Mismatched field identities.")
        if title_envelope["nonce"] == content_envelope["nonce"]:
            raise ValueError("Field nonces must be distinct.")
        if historical:
            previous = _envelope(instance.title, 16 * 1024)
            if any(previous[key] != title_envelope[key] for key in ["configuration_id", "entry_binding"]):
                raise ValueError("The existing encryption identity must be retained.")
        if not EncryptionPurpose.objects.filter(pk="diary", is_active=True).exists():
            raise ValueError("Diary encryption is temporarily disabled.")
        configurations = EncryptionConfiguration.objects.filter(
            pk=title_envelope["configuration_id"], user=account,
            entity_id=account.entity_id, format_version=1,
        )
        if not historical:
            configurations = configurations.filter(purposes__code="diary", is_active=True)
        configuration = configurations.first()
        if configuration is None:
            raise ValueError("An active personal Diary configuration is required.")
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValidationError({"message": "Invalid encrypted Diary fields or unavailable passphrase configuration."}) from None
