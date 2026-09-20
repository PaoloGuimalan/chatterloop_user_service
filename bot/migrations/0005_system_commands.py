"""Create the built-in commands the System bot answers.

WHY THE ROWS LIVE HERE AND THE CODE LIVES IN worker_service
------------------------------------------------------------
A command is two halves. This row is what makes `/members` RESOLVE - it is
what the parser's lookup finds, what scopes the command to a conversation, and
what `/help` reads to describe it. The function that answers is
`worker_service/internal/services/rabbitmq/builtins.go`, dispatched on the
NAME in this row.

That is why there is no codename column: one identifier, so the two halves
cannot disagree about which command this is.

A row with no function behind it logs a warning and answers nothing; a
function with no row is never reached. Both halves ship together, and this
migration is the half that has to exist before the first `/members` rather
than because of it.

WHY get_or_create AND NOT update
--------------------------------
`description` is editable, and a deploy that silently reverted somebody's
wording would be a deploy that quietly undid their work. This creates what is
missing and leaves what is there alone.
"""

from django.db import migrations

# category=system, responds=system for all three: chatterloop runs them, and
# the System bot says the answer. None of them is a state change, so none of
# them wants CommandResponder.NONE.
COMMANDS = [
    ("members", "List who is in this conversation."),
    ("created", "Show when this conversation started."),
    ("help", "List the commands available in this conversation."),
]


def create_system_commands(apps, schema_editor):
    Bot = apps.get_model("bot", "Bot")
    BotCommand = apps.get_model("bot", "BotCommand")

    # Created by 0004, which this depends on. Guarded anyway: a database where
    # that row was removed by hand should get a migration that does nothing,
    # not one that raises during a deploy.
    system = Bot.objects.filter(is_system=True, handle="system").first()
    if system is None:
        return

    for name, description in COMMANDS:
        BotCommand.objects.get_or_create(
            bot=system,
            name=name,
            defaults={
                "description": description,
                "category": "system",
                "responds": "system",
                "is_active": True,
            },
        )


def remove_system_commands(apps, schema_editor):
    """Removes only the rows this migration creates, and only the System bot's.

    Scoped to that bot on purpose: somebody else's `/help` is their command,
    and a reverse that deleted every row sharing a name would take it with it.
    """
    Bot = apps.get_model("bot", "Bot")
    BotCommand = apps.get_model("bot", "BotCommand")

    system = Bot.objects.filter(is_system=True, handle="system").first()
    if system is None:
        return

    BotCommand.objects.filter(
        bot=system, name__in=[name for name, _ in COMMANDS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("bot", "0004_system_bot"),
    ]

    operations = [
        migrations.RunPython(create_system_commands, remove_system_commands),
    ]
