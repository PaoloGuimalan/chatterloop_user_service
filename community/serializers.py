from rest_framework import serializers
from .models import Realm


class RealmSerializer(serializers.ModelSerializer):

    class Meta:
        model = Realm
        fields = "__all__"
