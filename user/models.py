import random
import uuid
from django.core.exceptions import ValidationError
from django.db import models, IntegrityError
from django.core.validators import EmailValidator
from django.utils.timezone import now

from cassandra.cqlengine import columns
from django_cassandra_engine.models import DjangoCassandraModel
from core.models import PolicyDocument

# Connection now lives in the entity app (it is an entity<->entity edge, not a
# user-only one). Re-exported here because historical migration user/0003
# references `user.models.generate_connection_id` and must stay resolvable.
#
# Block and Report moved the same way and for the same reason - every side of
# both is an Entity, so a page can be blocked or reported like a person.
# Re-exported so existing `from user.models import Block, Report` imports keep
# resolving; new code should import them from entity.models directly.
from entity.models import (  # noqa: F401
    Entity,
    Connection,
    Block,
    Report,
    generate_connection_id,
)

MINIMUM_AGE = 13


def generate_random_digit(digit):
    if digit < 1:
        raise ValueError("digit must be at least 1")
    start = 10 ** (digit - 1)
    end = 10**digit - 1
    return str(random.randint(start, end))


def generate_ver_code():
    return generate_random_digit(5)


def calculate_age(birthdate):
    if birthdate is None:
        return None
    today = now()
    age = today.year - birthdate.year
    if (today.month, today.day) < (birthdate.month, birthdate.day):
        age -= 1
    return age


class Account(models.Model):

    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
    ]

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, blank=True, primary_key=True
    )
    entity = models.OneToOneField(
        Entity, unique=True, on_delete=models.CASCADE, related_name="users"
    )
    username = models.CharField(max_length=150, unique=True, blank=True)
    first_name = models.CharField(max_length=150, null=False)
    middle_name = models.CharField(max_length=150, default="N/A")
    last_name = models.CharField(max_length=150, null=False)
    birthdate = models.DateTimeField(null=True, blank=True)
    profile = models.CharField(default="none")
    coverphoto = models.CharField(default="none")
    gender = models.CharField(
        max_length=150, null=True, blank=True, choices=GENDER_CHOICES
    )
    email = models.EmailField(unique=True, validators=[EmailValidator()])
    password = models.CharField(max_length=400, null=False, default=uuid.uuid4)
    date_created = models.DateTimeField(default=now)
    date_updated = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    is_badged = models.BooleanField(default=False)
    is_private = models.BooleanField(default=False)
    is_default_user = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    user_type = models.CharField(default="user", max_length=150, null=False)
    join_type = models.CharField(default="system", max_length=150, null=False)
    connection_count = models.IntegerField(default=0)
    ranking_score = models.FloatField(default=0.0, db_index=True)

    def is_authenticated(self):
        return True

    def get_age(self):
        return calculate_age(self.birthdate)

    def is_minor(self):
        age = self.get_age()
        return age is not None and age < MINIMUM_AGE

    def is_profile_complete(self):
        return bool(self.birthdate) and bool(self.gender)

    USERNAME_FIELD = "username"  # Use the username field for login
    REQUIRED_FIELDS = ["email"]  # Email is required but not for login

    def save(self, *args, **kwargs):
        if not self.username:
            prefix = self.first_name.split(" ")[0] + "_"
            prefix = prefix.lower()
            max_attempts = 5
            for _ in range(max_attempts):
                initial_un = prefix + generate_random_digit(3)
                listified_un = list(initial_un)
                random.shuffle(listified_un)
                self.username = "".join(listified_un)
                try:
                    super().save(*args, **kwargs)
                    break
                except IntegrityError:
                    # Collision happened, reset and retry
                    self.username = None
            else:
                raise IntegrityError(
                    "Could not generate a unique user_id after several attempts."
                )
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        return self.username


class Verification(models.Model):
    ver_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    user = models.ForeignKey(Account, null=False, on_delete=models.DO_NOTHING)
    ver_code = models.CharField(max_length=6, default=generate_ver_code, null=False)
    date_generated = models.DateTimeField(default=now)
    is_used = models.BooleanField(default=False)


class UserEngagementLog(DjangoCassandraModel):
    log_id = columns.UUID(primary_key=True, default=uuid.uuid4)
    user_id = columns.UUID(primary_key=True, partition_key=True)
    activity_time = columns.DateTime(primary_key=True, clustering_order="DESC")

    time_spent = columns.Float(required=False)
    activity_type = columns.Text()  # view, search, profile_visit, comment, share
    target_type = columns.Text(required=False)  # post, profile, search, realm, etc.
    target_id = columns.Text(required=False)
    metadata = columns.Text(required=False)  # JSON string for extra context
    created_at = columns.DateTime(default=now)
    updated_at = columns.DateTime(default=now)

    class Meta:
        get_pk_field = "log_id"


class UserConsent(models.Model):

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    entity = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="consents",
    )
    document_type = models.CharField(
        max_length=20, choices=PolicyDocument.DOCUMENT_TYPE_CHOICES
    )
    version = models.CharField(max_length=50)
    accepted_at = models.DateTimeField(default=now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        ordering = ["-accepted_at"]

    def __str__(self):
        return f"{str(self.entity.id)} accepted {self.document_type} {self.version}"
