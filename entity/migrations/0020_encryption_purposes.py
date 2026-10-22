from django.db import migrations, models


def link_existing_purposes(apps, schema_editor):
    configuration = apps.get_model("entity", "EncryptionConfiguration")
    purpose = apps.get_model("entity", "EncryptionPurpose")
    alias = schema_editor.connection.alias
    purpose.objects.using(alias).get_or_create(code="diary")
    links = configuration.purposes.through
    for row in configuration.objects.using(alias).only("pk", "verification_purpose").iterator():
        purpose.objects.using(alias).get_or_create(code=row.verification_purpose)
        links.objects.using(alias).get_or_create(
            encryptionconfiguration_id=row.pk, encryptionpurpose_id=row.verification_purpose,
        )


class Migration(migrations.Migration):
    dependencies = [("entity", "0019_encryptionconfiguration")]

    operations = [
        migrations.RemoveConstraint(model_name="encryptionconfiguration", name="unique_active_encryption_scope"),
        migrations.RemoveIndex(model_name="encryptionconfiguration", name="encryption_owner_purpose_idx"),
        migrations.RenameField(model_name="encryptionconfiguration", old_name="purpose", new_name="verification_purpose"),
        migrations.AlterField(model_name="encryptionconfiguration", name="verification_purpose", field=models.CharField(max_length=40)),
        migrations.CreateModel(
            name="EncryptionPurpose",
            fields=[
                ("code", models.CharField(max_length=40, primary_key=True, serialize=False)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"db_table": "entity_encryption_purpose", "ordering": ["code"]},
        ),
        migrations.AddField(
            model_name="encryptionconfiguration", name="purposes",
            field=models.ManyToManyField(related_name="configurations", to="entity.encryptionpurpose"),
        ),
        migrations.RunPython(link_existing_purposes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="encryptionconfiguration",
            constraint=models.UniqueConstraint(fields=("entity", "user"), condition=models.Q(is_active=True), name="unique_active_encryption_scope"),
        ),
        migrations.AddIndex(
            model_name="encryptionconfiguration",
            index=models.Index(fields=["entity", "user"], name="encryption_owner_idx"),
        ),
    ]
