from django.db import migrations, models


CHOICES = [('community', 'Community'), ('page', 'Page'), ('server', 'Server'),
           ('group', 'Group'), ('conference', 'Conference'), ('channel', 'Channel'), ('voice', 'Voice')]


def seed_types(apps, schema_editor):
    types = apps.get_model('community', 'Type').objects.using(schema_editor.connection.alias)
    for code, _ in CHOICES:
        types.get_or_create(code=code)


class Migration(migrations.Migration):
    dependencies = [('community', '0014_community_review')]
    operations = [
        migrations.CreateModel(name='Type', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('code', models.CharField(max_length=150, unique=True, choices=CHOICES)),
        ], options={'db_table': 'community_type', 'verbose_name': 'realm type',
                    'verbose_name_plural': 'realm types', 'ordering': ['code'],
                    'constraints': [models.CheckConstraint(condition=models.Q(code__in=[code for code, _ in CHOICES]),
                        name='community_type_known_code')]}),
        migrations.RunPython(seed_types, migrations.RunPython.noop),
    ]
