"""Create the System bot, the identity built-in commands answer under.

WHY A MIGRATION RATHER THAN A RUNTIME get_or_create
---------------------------------------------------
`get_system_moderator()` is called from wherever a moderation write needs it,
so the moderator appears the first time moderation runs. System has no such
moment: a built-in command is answered by worker_service, which reads this row
and does not own this schema, so nothing on the write path would create it. It
has to exist before the first `/members` rather than because of it.

Applying this on every deploy is the point - a fresh database, a restored
backup and a wiped dev box all converge on the same row.

WHY IT IS WRITTEN HERE INSTEAD OF IMPORTED
------------------------------------------
Migrations must describe the schema as it was when they were written.
Importing `get_system_bot` would run whatever that function becomes later,
against a historical model - the usual way a migration starts failing months
after it was applied. The constants are imported because they are the contract
every service agrees on; the writing is done against the historical model.
"""

from django.db import migrations

from bot.models import (
    SYSTEM_BOT_ENTITY_ID,
    SYSTEM_BOT_HANDLE,
    SYSTEM_BOT_NAME,
)

DESCRIPTION = "Answers built-in chat commands such as /members and /created."


def create_system_bot(apps, schema_editor):
    Entity = apps.get_model("entity", "Entity")
    Bot = apps.get_model("bot", "Bot")

    entity, _ = Entity.objects.get_or_create(
        id=SYSTEM_BOT_ENTITY_ID,
        defaults={"type": "bot"},
    )

    Bot.objects.get_or_create(
        entity=entity,
        defaults={
            "name": SYSTEM_BOT_NAME,
            "handle": SYSTEM_BOT_HANDLE,
            "description": DESCRIPTION,
            "is_system": True,
        },
    )


def remove_system_bot(apps, schema_editor):
    """Reversible, but it takes the entity with it.

    Deliberate: an Entity with no Bot behind it is the exact state the bot app
    was created to fix - every surface resolving that entity falls through to a
    raw UUID. Leaving one behind to be tidy would leave a worse row than no row.
    """
    Entity = apps.get_model("entity", "Entity")
    Bot = apps.get_model("bot", "Bot")

    Bot.objects.filter(entity_id=SYSTEM_BOT_ENTITY_ID).delete()
    Entity.objects.filter(id=SYSTEM_BOT_ENTITY_ID).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("bot", "0003_botcommand"),
        ("entity", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_system_bot, remove_system_bot),
    ]
