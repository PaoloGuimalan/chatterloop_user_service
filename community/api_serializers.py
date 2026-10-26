"""Input validation for community lifecycle endpoints."""

from rest_framework import serializers


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail("invalid", input=data)
        return data


class CommunityCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    slug = serializers.CharField()
    is_private = StrictBooleanField(required=False, default=False)
