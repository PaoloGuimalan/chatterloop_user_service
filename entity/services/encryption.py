"""Personal encryption-configuration lifecycle. All writers lock the Account.

    is_active selects future encryption; it never revokes historical decryption.
"""
import hashlib
import hmac
from copy import deepcopy

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils.timezone import now

from entity.encryption_contract import CONSUMER_CAPABILITIES, SUPPORTED_PROFILE, canonical_json, decode_base64
from entity.encryption_serializers import EncryptionConfigurationSerializer
from entity.models import EncryptionConfiguration, EncryptionPurpose
from user.models import Account


class EncryptionError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _owner(account, lock=False):
    query = Account.objects
    if lock:
        query = query.select_for_update()
    owner = query.filter(pk=getattr(account, "pk", None), is_active=True).first()
    if owner is None or owner.entity.type != "user":
        raise EncryptionError("invalid_owner", "Personal account unavailable.", 403)
    return owner


def _rows(owner, purpose):
    return list(EncryptionConfiguration.objects.filter(
        user=owner, entity_id=owner.entity_id, purposes__code=purpose,
    ).prefetch_related("purposes").order_by("id"))


def _version(owner, purpose):
    # Activation is configuration-wide, so a change in another purpose must
    # invalidate stale confirmations too.
    configurations = EncryptionConfiguration.objects.filter(
        user=owner, entity_id=owner.entity_id,
    ).only("id", "revision", "is_active").prefetch_related("purposes").order_by("id")
    catalog = list(EncryptionPurpose.objects.order_by("code").values_list("code", "is_active"))
    snapshot = ["chatterloop.encryption.scope", 2, str(owner.entity_id), str(owner.pk), purpose, catalog,
                [[str(row.pk), row.revision, row.is_active,
                  sorted(item.code for item in row.purposes.all())] for row in configurations]]
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()


def _group(owner, purpose, rows=None):
    rows = _rows(owner, purpose) if rows is None else rows
    return {
        "purpose": purpose, "label": purpose,
        "is_active": EncryptionPurpose.objects.get(pk=purpose).is_active,
        "configurations": EncryptionConfigurationSerializer(rows, many=True).data,
        "active_configuration_id": next((str(row.pk) for row in rows if row.is_active), None),
        "scope_version": _version(owner, purpose),
    }


def _check_version(owner, purpose, rows, data):
    if data.get("expected_scope_version") != _version(owner, purpose):
        raise EncryptionError("stale_scope", "Configurations changed. Reload and confirm again.", 409)


def _target(owner, configuration_id):
    row = EncryptionConfiguration.objects.filter(pk=configuration_id, user=owner, entity_id=owner.entity_id).prefetch_related("purposes").first()
    if row is None or not row.purposes.all():
        raise EncryptionError("not_found", "Configuration not found.", 404)
    return row


def _context(row, data, *, require_active=False):
    memberships = sorted(item.code for item in row.purposes.all())
    purpose = data.get("purpose") or (row.verification_purpose if row.verification_purpose in memberships else memberships[0])
    if purpose not in memberships:
        raise EncryptionError("unsupported_purpose", "Configuration is not available for this purpose.")
    if require_active and not next(item for item in row.purposes.all() if item.code == purpose).is_active:
        raise EncryptionError("purpose_inactive", "Encryption is temporarily disabled for this purpose.", 409)
    return purpose


def _check_proof(row, proof):
    try:
        token = decode_base64(proof, 32, 32)
    except ValueError:
        raise EncryptionError("unlock_failed", "Could not confirm the passphrase.", 403) from None
    if not hmac.compare_digest(hashlib.sha256(token).hexdigest(), row.verification_digest):
        raise EncryptionError("unlock_failed", "Could not confirm the passphrase.", 403)


def get_personal_configurations(account, purpose=None):
    owner = _owner(account)
    if purpose is not None and not EncryptionPurpose.objects.filter(pk=purpose).exists():
        raise EncryptionError("unsupported_purpose", "Unsupported purpose.")
    purposes = list(EncryptionPurpose.objects.values_list("code", flat=True)) if purpose is None else [purpose]
    return {"owner": {"entity_id": str(owner.entity_id), "user_id": str(owner.pk)},
            "consumer_capabilities": deepcopy(CONSUMER_CAPABILITIES),
            "supported_profile": deepcopy(SUPPORTED_PROFILE),
            "purposes": [_group(owner, item) for item in purposes]}


@transaction.atomic
def register_configuration(account, validated_data):
    owner = _owner(account, lock=True)
    data = dict(validated_data)
    expected_version = data.pop("expected_scope_version", None)
    purpose = data.pop("purpose", None)
    purposes = data.pop("purposes", [purpose])
    catalog = {row.code: row for row in EncryptionPurpose.objects.filter(code__in=purposes)}
    if not purposes or purpose not in purposes or len(set(purposes)) != len(purposes) or set(purposes) != set(catalog):
        raise EncryptionError("unsupported_purpose", "Unsupported purpose.")
    if any(not row.is_active for row in catalog.values()):
        raise EncryptionError("purpose_inactive", "Encryption is temporarily disabled for this purpose.", 409)
    data["verification_purpose"] = purpose
    replaces_id = data.pop("replaces", None)
    data["replaces_id"] = replaces_id
    existing = EncryptionConfiguration.objects.filter(pk=data["id"]).first()
    if existing is not None:
        identical = str(existing.user_id) == str(owner.pk) and str(existing.entity_id) == str(owner.entity_id)
        for field, value in data.items():
            stored = getattr(existing, field)
            identical = identical and (str(stored) == str(value) if field in ("id", "replaces_id") else stored == value)
        identical = identical and set(existing.purposes.values_list("code", flat=True)) == set(purposes)
        if not identical:
            raise EncryptionError("configuration_conflict", "Configuration ID is unavailable.", 409)
        return _group(owner, purpose), False
    rows = _rows(owner, purpose)
    _check_version(owner, purpose, rows, {"expected_scope_version": expected_version})
    if replaces_id is not None and not any(str(row.pk) == str(replaces_id) for row in rows):
        raise EncryptionError("invalid_predecessor", "Predecessor must belong to the same configuration scope.")
    try:
        # Savepoint keeps a cross-owner simultaneous UUID collision recoverable.
        with transaction.atomic():
            configuration = EncryptionConfiguration.objects.create(entity_id=owner.entity_id, user=owner, **data)
            configuration.purposes.set([catalog[item] for item in purposes])
    except IntegrityError:
        raise EncryptionError("configuration_conflict", "Configuration ID is unavailable.", 409) from None
    return _group(owner, purpose), True


@transaction.atomic
def rename_configuration(account, configuration_id, validated_data):
    owner = _owner(account, lock=True)
    row = _target(owner, configuration_id)
    purpose = _context(row, validated_data)
    _check_version(owner, purpose, _rows(owner, purpose), validated_data)
    row.name = validated_data["name"]
    row.revision += 1
    row.save(update_fields=["name", "revision", "updated_at"])
    return _group(owner, purpose)


@transaction.atomic
def activate_configuration(account, configuration_id, validated_data):
    owner = _owner(account, lock=True)
    target = _target(owner, configuration_id)
    purpose = _context(target, validated_data, require_active=True)
    rows = _rows(owner, purpose)
    _check_version(owner, purpose, rows, validated_data)
    current = EncryptionConfiguration.objects.filter(user=owner, entity_id=owner.entity_id, is_active=True).first()
    if target.is_active:
        raise EncryptionError("already_active", "Configuration is already active.", 409)
    _check_proof(target, validated_data.get("target_verification_proof"))
    if current:
        _check_proof(current, validated_data.get("active_verification_proof"))
    stamp = now()
    if current:
        current.is_active = False
        current.deactivated_at = stamp
        current.revision += 1
        current.save(update_fields=["is_active", "deactivated_at", "revision", "updated_at"])
    target.is_active = True
    target.deactivated_at = None
    target.revision += 1
    target.save(update_fields=["is_active", "deactivated_at", "revision", "updated_at"])
    return _group(owner, purpose)


@transaction.atomic
def deactivate_configuration(account, configuration_id, validated_data):
    owner = _owner(account, lock=True)
    target = _target(owner, configuration_id)
    purpose = _context(target, validated_data)
    _check_version(owner, purpose, _rows(owner, purpose), validated_data)
    if not target.is_active:
        raise EncryptionError("not_active", "Configuration is not active.", 409)
    _check_proof(target, validated_data.get("active_verification_proof"))
    target.is_active = False
    target.deactivated_at = now()
    target.revision += 1
    target.save(update_fields=["is_active", "deactivated_at", "revision", "updated_at"])
    return _group(owner, purpose)


@transaction.atomic
def retire_account_configurations(account):
    """Account deletion only: retain metadata, stop selecting future encryption."""
    owner = Account.objects.select_for_update().get(pk=account.pk)
    stamp = now()
    return EncryptionConfiguration.objects.filter(user=owner, is_active=True).update(
        is_active=False, deactivated_at=stamp, updated_at=stamp, revision=F("revision") + 1,
    )
