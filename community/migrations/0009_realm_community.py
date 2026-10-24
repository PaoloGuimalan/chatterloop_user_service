from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("community", "0008_invite_purpose_role")]
    operations = [
        migrations.AlterField(
            model_name="realm",
            name="type",
            field=models.CharField(
                max_length=150,
                choices=[
                    ("community", "Community"),
                    ("page", "Page"),
                    ("server", "Server"),
                    ("group", "Group"),
                    ("conference", "Conference"),
                    ("channel", "Channel"),
                    ("voice", "Voice"),
                ],
            ),
        ),
    ]
