import uuid
import random
from django.db import models
from user.models import Account


def generate_random_digit(digit):
    if digit < 1:
        raise ValueError("digit must be at least 1")
    start = 10 ** (digit - 1)
    end = 10**digit - 1
    return str(random.randint(start, end))


class Realm(models.Model):

    REALM_TYPE_CHOICES = [
        ("community", "Community"),
        ("page", "Page"),
        ("server", "Server"),
        ("group", "Group"),
    ]

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, blank=True, primary_key=True
    )
    realm_id = models.CharField(
        max_length=150,
        default=f"{generate_random_digit(15)}",
        unique=True,
    )
    name = models.CharField(max_length=150, null=False)
    profile = models.CharField(default="N/A")
    cover_photo = models.CharField(blank=True, null=True, default=None)
    description = models.TextField(blank=True, null=True, default=None)
    created_by = models.ForeignKey(
        Account,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="realm_as_created_by",
    )
    type = models.CharField(max_length=150, null=False, choices=REALM_TYPE_CHOICES)
    parent = models.ForeignKey(
        "self",
        to_field="realm_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)
    is_private = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)


class Member(models.Model):
    member_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, blank=True, primary_key=True
    )
    account = models.ForeignKey(
        Account,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="user_as_member",
    )
    nickname = models.CharField(max_length=150, null=True, blank=True)
    realm = models.ForeignKey(
        Realm,
        null=False,
        on_delete=models.DO_NOTHING,
    )
    added_by = models.ForeignKey(
        Account,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="user_as_added_by",
    )
    date_joined = models.DateTimeField(null=True)
