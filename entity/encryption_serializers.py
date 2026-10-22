"""Explicit, non-overlapping read/write schemas for encryption configurations."""
from collections.abc import Mapping

from rest_framework import serializers

from entity.encryption_contract import decode_base64, verification_aad
from entity.models import EncryptionConfiguration, EncryptionPurpose


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Unexpected or invalid fields."]})
        return super().to_internal_value(data)


class TextField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class IntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class ChoiceField(serializers.ChoiceField):
    default_error_messages = {"invalid_choice": "Unsupported value."}


class PurposeCodeField(TextField):
    def __init__(self, **kwargs):
        super().__init__(min_length=1, max_length=40, trim_whitespace=False, **kwargs)

    def to_internal_value(self, data):
        code = super().to_internal_value(data)
        if not EncryptionPurpose.objects.filter(pk=code).exists():
            raise serializers.ValidationError("Unknown encryption purpose.")
        return code


class CanonicalUUIDField(serializers.UUIDField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if str(value) != data:
            self.fail("invalid")
        return value


class BinaryEnvelopeField(TextField):
    def __init__(self, minimum, maximum, **kwargs):
        self.minimum, self.maximum = minimum, maximum
        super().__init__(trim_whitespace=False, max_length=4 * ((maximum + 2) // 3), **kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            decode_base64(value, self.minimum, self.maximum)
        except ValueError:
            raise serializers.ValidationError("Invalid binary envelope.") from None
        return value


class KDFParametersSerializer(StrictSerializer):
    version = IntegerField(min_value=19, max_value=19)
    memory_kib = IntegerField(min_value=65536, max_value=65536)
    iterations = IntegerField(min_value=3, max_value=3)
    parallelism = IntegerField(min_value=1, max_value=1)
    output_length = IntegerField(min_value=32, max_value=32)


class ScopeSerializer(StrictSerializer):
    expected_scope_version = serializers.RegexField(r"\A[0-9a-f]{64}\Z", min_length=64, max_length=64, trim_whitespace=False)


class CreateConfigurationSerializer(ScopeSerializer):
    id = CanonicalUUIDField()
    purpose = PurposeCodeField()
    purposes = serializers.ListField(child=PurposeCodeField(), allow_empty=False, required=False)
    name = TextField(min_length=1, max_length=80)
    format_version = IntegerField(min_value=1, max_value=1)
    kdf_algorithm = ChoiceField(choices=("argon2id",))
    kdf_parameters = KDFParametersSerializer()
    salt = BinaryEnvelopeField(16, 16)
    cipher_algorithm = ChoiceField(choices=("aes-256-gcm",))
    verification_nonce = BinaryEnvelopeField(12, 12)
    verification_ciphertext = BinaryEnvelopeField(17, 1024)
    verification_digest = serializers.RegexField(r"\A[0-9a-f]{64}\Z", min_length=64, max_length=64, trim_whitespace=False, write_only=True)
    replaces = CanonicalUUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        purposes = attrs.get("purposes", [attrs["purpose"]])
        if len(purposes) != len(set(purposes)) or attrs["purpose"] not in purposes:
            raise serializers.ValidationError("Choose distinct purposes including the verification purpose.")
        attrs["purposes"] = sorted(purposes)
        return attrs


class ConfigurationContextSerializer(ScopeSerializer):
    purpose = PurposeCodeField(required=False)


class RenameConfigurationSerializer(ConfigurationContextSerializer):
    name = TextField(min_length=1, max_length=80)


class ActivateConfigurationSerializer(ConfigurationContextSerializer):
    active_verification_proof = BinaryEnvelopeField(32, 32, required=False, write_only=True)
    target_verification_proof = BinaryEnvelopeField(32, 32, write_only=True)


class DeactivateConfigurationSerializer(ConfigurationContextSerializer):
    active_verification_proof = BinaryEnvelopeField(32, 32, write_only=True)


class EncryptionConfigurationSerializer(serializers.ModelSerializer):
    verification_aad = serializers.SerializerMethodField()
    # Keep v1 clients working during a staggered backend/client deployment.
    purpose = serializers.CharField(source="verification_purpose", read_only=True)
    purposes = serializers.SlugRelatedField(many=True, slug_field="code", read_only=True)

    class Meta:
        model = EncryptionConfiguration
        fields = (
            "id", "entity_id", "user_id", "purpose", "purposes", "verification_purpose", "name", "is_active", "revision",
            "format_version", "kdf_algorithm", "kdf_parameters", "salt", "cipher_algorithm",
            "verification_nonce", "verification_ciphertext", "verification_aad", "replaces",
            "created_at", "updated_at", "deactivated_at",
        )
        read_only_fields = fields

    def get_verification_aad(self, obj):
        return verification_aad(obj)
