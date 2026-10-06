# Hand-written (makemigrations re-emits churn for the random-default fields -
# see the note on Realm.id / generate_realm_uid), containing only these three
# operations.
#
# Invites by username store no email, so target_email becomes optional, and an
# invite now says what accepting does (purpose) and, for a page's team, which
# role. Every existing row is a conference/realm "join", which the default
# gives it.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0007_delete_realmfollow"),
    ]

    operations = [
        migrations.AlterField(
            model_name="invite",
            name="target_email",
            field=models.EmailField(
                blank=True, db_index=True, max_length=254, null=True
            ),
        ),
        migrations.AddField(
            model_name="invite",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("join", "Join"),
                    ("manage", "Manage"),
                    ("follow", "Follow"),
                ],
                default="join",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="invite",
            name="role",
            field=models.CharField(
                blank=True, default=None, max_length=20, null=True
            ),
        ),
    ]
