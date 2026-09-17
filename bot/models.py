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

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils.timezone import now

from entity.models import Entity, EntityType
from entity.permissions import MemberRole

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

# The command responder, and the second fixed-id platform bot. Same reasoning
# as the moderator above: every service that renders a system reply has to
# agree on who "the platform" is, and a generated id would differ per
# environment.
#
# It answers /members and /created - conversation facts, immediately, with no
# model call - and posts under this identity so those replies have a name and
# an avatar instead of falling through to a raw UUID.
#
# It is NOT a conversation member and holds NO token. Nothing can authenticate
# as it through developer_service, because there is no credential to present;
# the only thing that can speak as System is the platform writing the message
# itself. That is a stronger guarantee than any permission check, and it costs
# nothing - it is the absence of a row.
SYSTEM_BOT_ENTITY_ID = "00000000-0000-4000-8000-000000000002"
SYSTEM_BOT_HANDLE = "system"
SYSTEM_BOT_NAME = "System"


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

    # The badge - same concept as Account.is_badged and Realm.is_verified,
    # kept under this app's own name rather than reusing either of theirs
    # because a bot's verification has nothing to do with email confirmation
    # (Account.is_verified) and nothing to do with Realm's page-verification
    # flow; it is its own decision, made by whoever administers this table.
    #
    # Default False: an ordinary user-owned bot is not verified just by
    # existing, the same way an ordinary account is not badged just by
    # existing. `is_system` bots (the moderator) are a separate concept and
    # are not verified by default either - the two flags answer different
    # questions ("is this bot platform-owned" vs "should this bot show a
    # badge") and a caller that wants both true sets both explicitly.
    is_verified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(default=now)

    class Meta:
        indexes = [
            models.Index(fields=["owner_entity"], name="bot_owner_idx"),
        ]

    def __str__(self):
        return f"{self.name} (@{self.handle})"


def get_system_bot() -> Bot:
    """The platform's command responder, creating it if this database has never
    seen it.

    Idempotent and keyed on a FIXED entity id, exactly like
    `get_system_moderator` below - a restart, a fresh clone or two services
    calling it at once all converge on the same row rather than minting a
    second System.

    `is_system=True` is load-bearing beyond bookkeeping: entity search excludes
    system bots, so this one is not discoverable, joinable or addressable. Its
    handle exists to render a name, not to be typed at.
    """
    entity, _ = Entity.objects.get_or_create(
        id=SYSTEM_BOT_ENTITY_ID,
        defaults={"type": EntityType.BOT_CHOICE},
    )

    bot, _ = Bot.objects.get_or_create(
        entity=entity,
        defaults={
            "name": SYSTEM_BOT_NAME,
            "handle": SYSTEM_BOT_HANDLE,
            "description": (
                "Answers built-in chat commands such as /members and /created."
            ),
            "is_system": True,
        },
    )
    return bot


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


# ---------------------------------------------------------------- commands --


class CommandCategory(models.TextChoices):
    """WHO executes a command.

    SYSTEM   chatterloop runs it, from the worker map keyed on the name.
    WEBHOOK  an HTTP request. The only one with a delivery receipt, which is
             why it is the one that can reach a bot that is offline.
    BOT      an SSE trigger to a bot already listening. Free to wire, and lost
             without a trace if the bot is not there.
    """

    SYSTEM = "system", "System"
    WEBHOOK = "webhook", "Webhook"
    BOT = "bot", "Bot"


class CommandResponder(models.TextChoices):
    """Who says something afterwards, if anyone.

    NONE is a real answer: /stop changes state and has nothing to report, and
    posting "stopped" into the thread somebody just asked to quieten is wrong.
    """

    NONE = "none", "Nothing"
    SYSTEM = "system", "The system bot"
    BOT = "bot", "The owning bot"


def default_webhook_request():
    # A callable, not a literal - a shared mutable default would let one row
    # edit another.
    return {"payload": {}, "headers": {}, "query": {}, "params": {}}


def _hint(value):
    """The last four characters, enough to tell two credentials apart and not
    enough to use one."""
    text = value if isinstance(value, str) else str(value)
    return f"...{text[-4:]}" if len(text) > 4 else "..."


class BotCommand(models.Model):
    """A /command somebody can type in a conversation.

    Reach is not stored - a command is usable exactly where its bot is. An
    ordinary bot reaches the conversations it belongs to; a system bot is
    exempt and reaches everywhere. A scope column would be a second source of
    truth, disagreeing the first time a bot left a realm.

    `bot` is never null, because the system bot is a bot.

    The worker maps on the NAME - /members looks up "members" - so there is no
    codename to drift. A name the running worker has no function for is
    possible after a rollback; it answers "not available here" rather than
    doing nothing, since silence is indistinguishable from a typo.
    """

    # What a client may be told. An ALLOW-list rather than excluding the
    # dangerous fields, so a column added later is private by default instead
    # of leaking until somebody notices. ToolSerializer in Neon was
    # `fields = "__all__"`, which is how a tool credential ended up serialised
    # into the model's own prompt.
    PUBLIC_FIELDS = ("name", "description", "responds")

    id = models.CharField(
        max_length=40, default=uuid.uuid4, unique=True, primary_key=True
    )

    # The word after the slash. Same shape as a handle so nobody learns a
    # second rule, and no ":" because that separates name from target.
    name = models.CharField(
        max_length=32,
        validators=[
            RegexValidator(
                regex=r"^[a-z0-9-]{1,32}$",
                message="Use lowercase letters, digits and hyphens only.",
            )
        ],
    )

    # What autocomplete shows beside the name. Without it a user has to already
    # know what /summarize does, which defeats discovery.
    description = models.CharField(max_length=200, blank=True, default="")

    category = models.CharField(
        max_length=20, choices=CommandCategory.choices, default=CommandCategory.BOT
    )

    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="commands")

    responds = models.CharField(
        max_length=20, choices=CommandResponder.choices, default=CommandResponder.BOT
    )

    # WEBHOOK only.
    webhook_url = models.URLField(max_length=500, blank=True, default="")

    # WEBHOOK only. Four optional parts of the outbound request:
    #   payload  merged into the JSON body
    #   headers  added to the request
    #   query    appended to the query string
    #   params   substituted into {placeholders} in webhook_url
    #
    # SECRETS LIVE HERE IN PLAIN TEXT. An Authorization header put in `headers`
    # is readable by anything that can read this table. Never serialise it
    # directly - `redacted_request()` is what a management screen shows.
    webhook_request = models.JSONField(default=default_webhook_request, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "bot_commands"

        indexes = [
            # The resolution path, read on every message starting with a slash.
            models.Index(fields=["name", "is_active"], name="botcmd_name_active_idx"),
            # No index on `bot` - the foreign key already creates one, and a
            # second on the same column is only another write to maintain.
        ]

        constraints = [
            # Two DIFFERENT bots may share a name - that is fan-out. One bot
            # defining it twice is the mistake being prevented.
            models.UniqueConstraint(fields=["bot", "name"], name="botcmd_unique_per_bot"),
            # A webhook row with no URL resolves fine and fails at dispatch.
            models.CheckConstraint(
                condition=(~Q(category="webhook") | ~Q(webhook_url="")),
                name="botcmd_webhook_has_url",
            ),
            models.CheckConstraint(
                condition=(Q(category="webhook") | Q(webhook_url="")),
                name="botcmd_url_only_on_webhook",
            ),
        ]

    def public(self):
        """What a chat client is told: enough to autocomplete, nothing more.

        The owning handle is added by the caller, which already has the bot -
        touching `self.bot` here would be a query per command in a listing.
        """
        return {field: getattr(self, field) for field in self.PUBLIC_FIELDS}

    def redacted_request(self):
        """`webhook_request` with every value masked to its last four
        characters.

        For whoever manages the command: they need to see THAT an Authorization
        header is set, and which of two credentials it is, without the value
        being readable from a screen, a log or an export. The same trade
        ProviderCredential.api_key_hint makes in Neon.
        """
        request = self.webhook_request or {}
        return {
            section: {key: _hint(value) for key, value in (part or {}).items()}
            for section, part in request.items()
            if isinstance(part, dict)
        }

    def __str__(self):
        return f"/{self.name} ({self.category}, @{self.bot.handle})"
