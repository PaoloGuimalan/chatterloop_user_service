"""Let a webhook command choose its HTTP verb.

Every webhook command was a POST, because worker_service hardcoded one. A
webhook points at somebody else's API though, and that API's shape is theirs -
a status endpoint is a GET, a toggle is often a PUT, an unsubscribe a DELETE.
Against those, a POST answers 405 and the command looks broken for a reason
nothing in the conversation explains.

NULLABLE, AND NOT AS A CONVENIENCE
----------------------------------
A `system` or `bot` command makes no HTTP request at all. Giving those rows a
verb would put a value in the column that describes nothing, and a column that
says POST for a command that never calls anything is one nobody can read an
answer out of. They hold NULL, and `botcmd_method_only_on_webhook` keeps it
that way.

SAFE ON AN EXISTING DATABASE
----------------------------
The column arrives NULL everywhere, and existing WEBHOOK rows are then set to
POST - which is exactly what they already did. So the deploy changes no
behaviour, and afterwards every webhook row says out loud which verb it uses
instead of relying on a default nobody can see.

A webhook row may still be NULL - worker_service reads that as POST - so a
caller that does not care about the verb does not have to name one.
"""

from django.db import migrations, models
from django.db.models import Q


def state_the_existing_verb(apps, schema_editor):
    BotCommand = apps.get_model("bot", "BotCommand")
    BotCommand.objects.filter(category="webhook").update(webhook_method="POST")


def clear_the_verb(apps, schema_editor):
    """Reverse leaves the column empty rather than guessing.

    Going backwards removes the column entirely in the operation below, so
    this only exists to keep the pair symmetrical.
    """
    BotCommand = apps.get_model("bot", "BotCommand")
    BotCommand.objects.update(webhook_method=None)


class Migration(migrations.Migration):

    dependencies = [
        ("bot", "0005_system_commands"),
    ]

    operations = [
        migrations.AddField(
            model_name="botcommand",
            name="webhook_method",
            field=models.CharField(
                choices=[
                    ("GET", "GET"),
                    ("POST", "POST"),
                    ("PUT", "PUT"),
                    ("PATCH", "PATCH"),
                    ("DELETE", "DELETE"),
                ],
                default=None,
                max_length=10,
                null=True,
                blank=True,
            ),
        ),
        migrations.RunPython(state_the_existing_verb, clear_the_verb),
        migrations.AddConstraint(
            model_name="botcommand",
            constraint=models.CheckConstraint(
                condition=(
                    Q(category="webhook") | Q(webhook_method__isnull=True)
                ),
                name="botcmd_method_only_on_webhook",
            ),
        ),
    ]
