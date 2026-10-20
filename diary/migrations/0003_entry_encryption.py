from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("diary", "0002_move_tags_to_interests"),
        ("entity", "0019_encryptionconfiguration"),
    ]

    operations = [
        migrations.AlterField(model_name="entry", name="title", field=models.TextField(blank=True)),
        migrations.AddField(model_name="entry", name="is_encrypted", field=models.BooleanField(default=False)),
    ]
