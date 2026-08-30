"""
Bots - non-human entities that act on the platform.

WHY AN APP RATHER THAN A COLUMN
-------------------------------
`EntityType.BOT_CHOICE` already existed; nothing backed it. An Entity of type
"bot" therefore had no name, no picture and no owner, which meant every surface
that resolves an entity's identity fell through to `str(entity.id)` and rendered
a raw UUID. This app is the missing half of that: the row a bot entity points
at, the same way a user entity points at an Account and a realm entity points at
a Realm.

It is deliberately GENERAL. The system moderator is the first bot and currently
the only one, but the model carries an owner and a description because the next
ones will be user-owned - and retrofitting an ownership column onto rows that
already exist is a migration nobody enjoys.

WHAT THIS DOES NOT DO
---------------------
No credentials, no API keys, no permissions of its own. A bot acts through its
Entity, which is what every permission check in the platform already keys on, so
a bot is exactly as capable as its entity is and no more. Authentication for
user-owned bots is a separate problem and is not solved here.
"""

import uuid

from django.db import models
from django.utils.timezone import now

from entity.models import Entity, EntityType

# The system moderator's entity id, FIXED so that every service can address it
# without a lookup table or a config value that can drift between environments.
#
# Hardcoded on purpose: the moderation service, the notification writer and the
# report filer all need to agree on who "the platform" is, and a generated id
# would have to be discovered at runtime by all three - with a different answer
# in every environment. get_or_create means a fresh database, a restarted
# service and a wiped dev box all converge on this same row.
SYSTEM_MODERATOR_ENTITY_ID = "00000000-0000-4000-8000-000000000001"
SYSTEM_MODERATOR_HANDLE = "moderator"
SYSTEM_MODERATOR_NAME = "Chatterloop Moderation"


class Bot(models.Model):
    """
    A bot's identity - what a person sees when a bot appears in their feed,
    their notifications or a report.

    `entity` is the key fact. Everything else on this row is presentation.
    """

    id = models.CharField(
        max_length=40, default=uuid.uuid4, unique=True, primary_key=True
    )

    # OneToOne, mirroring Account.entity and Realm.entity - and named `bots` in
    # reverse for the same reason those are named `users` and `realms`:
    # entity.utils resolves an entity's identity by trying each relation in
    # turn, and it can only try relations that exist.
    entity = models.OneToOneField(
        Entity,
        unique=True,
        on_delete=models.CASCADE,
        related_name="bots",
    )

    name = models.CharField(max_length=80)

    # The @handle. Unique across bots only - a bot and a user CAN currently
    # share a handle, which is a real gap but not one this app can close alone:
    # handles live on Account, Realm and now here, with no shared registry.
    handle = models.CharField(max_length=50, unique=True)

    description = models.TextField(blank=True, default="")

    # Same shape as Account.profile / Realm.profile - a URL or the string
    # sentinel those use for "none". Kept as a plain CharField rather than an
    # ImageField because every other profile picture on this platform is a CDN
    # URL written by the upload service, not a Django-managed file.
    profile = models.CharField(max_length=500, default="none")

    # Who runs it. NULL for platform bots, which answer to nobody - and the
    # reason this is nullable rather than pointing at some placeholder entity.
    owner_entity = models.ForeignKey(
        Entity,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="owned_bots",
    )

    # Platform-owned and not deletable through any user-facing path. The
    # moderator is one; a user's own bot is not.
    is_system = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(default=now)

    class Meta:
        indexes = [
            models.Index(fields=["owner_entity"], name="bot_owner_idx"),
        ]

    def __str__(self):
        return f"{self.name} (@{self.handle})"


def get_system_moderator() -> Bot:
    """
    The platform's moderation bot, creating it if this database has never seen
    it.

    Idempotent and safe to call from anywhere, including on every service
    start: it is keyed on a FIXED entity id, so a restart, a fresh clone or a
    second service calling it concurrently all converge on the same row rather
    than minting a second moderator.

    Returns the Bot; `bot.entity_id` is what report and notification writers
    want.
    """
    entity, _ = Entity.objects.get_or_create(
        id=SYSTEM_MODERATOR_ENTITY_ID,
        defaults={"type": EntityType.BOT_CHOICE},
    )

    bot, _ = Bot.objects.get_or_create(
        entity=entity,
        defaults={
            "name": SYSTEM_MODERATOR_NAME,
            "handle": SYSTEM_MODERATOR_HANDLE,
            "description": (
                "Reviews posts, comments and attachments against the "
                "community guidelines."
            ),
            "is_system": True,
        },
    )
    return bot
