import random
import uuid
from django.core.exceptions import ValidationError
from django.db import models, IntegrityError
from django.core.validators import EmailValidator
from django.utils.timezone import now


def generate_random_digit(digit):
    if digit < 1:
        raise ValueError("digit must be at least 1")
    start = 10 ** (digit - 1)
    end = 10**digit - 1
    return str(random.randint(start, end))


class Account(models.Model):

    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
    ]

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, blank=True, primary_key=True
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
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    is_default_user = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    join_type = models.CharField(default="system", max_length=150, null=False)

    def is_authenticated(self):
        return True

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
    ver_code = models.CharField(
        max_length=6, default=generate_random_digit(5), null=False
    )
    date_generated = models.DateTimeField(default=now)
    is_used = models.BooleanField(default=False)


class Connection(models.Model):

    CONNECTION_TYPE_CHOICES = [
        ("single", "Single"),
    ]

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    connection_id = models.CharField(max_length=150, default=generate_random_digit(20))
    action_by = models.ForeignKey(
        Account,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="connections_as_action_by",
    )
    nickname = models.CharField(max_length=150, null=True, blank=True)
    status = models.BooleanField(default=True)
    involved_user = models.ForeignKey(
        Account,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="connections_as_involved_user",
    )
    action_date = models.DateTimeField(default=now)
    type = models.CharField(max_length=150, null=False, choices=CONNECTION_TYPE_CHOICES)

    def clean(self):
        super().clean()

        if self.type == "single":
            # Count records with the same connection_id and type "single"
            existing_connections = Connection.objects.filter(
                connection_id=self.connection_id, type="single"
            ).exclude(pk=self.pk)

            if existing_connections.count() == 2:
                raise ValidationError(
                    "Single connection can only involve two users total."
                )

            user_in_use = Connection.objects.filter(
                connection_id=self.connection_id,
                type="single",
                involved_user=self.involved_user,
            ).exclude(pk=self.pk)

            if user_in_use.exists():
                raise ValidationError(
                    "This involved user is already part of the single connection."
                )

            if self.action_by != self.involved_user:
                connection_triggered = Connection.objects.filter(
                    type="single",
                    involved_user=self.involved_user,
                    action_by=self.action_by,  # checking if action already existing
                ).exclude(pk=self.pk)

                if connection_triggered.exists():
                    raise ValidationError("Connection is already existing.")

                user_initiated = Connection.objects.filter(
                    type="single",
                    involved_user=self.action_by,
                    action_by=self.involved_user,  # checking if involved_user is action_by
                ).exclude(pk=self.pk)

                if user_initiated.exists():
                    raise ValidationError(
                        "This involved user has already initiated a single connection."
                    )

    def save(self, *args, **kwargs):
        self.full_clean()  # Calls clean() and validates
        super().save(*args, **kwargs)
