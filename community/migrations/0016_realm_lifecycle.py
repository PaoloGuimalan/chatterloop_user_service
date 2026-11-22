from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('community', '0015_realm_types')]
    operations = [
        migrations.AddField(model_name='realm', name='deactivated_at',
                            field=models.DateTimeField(null=True, blank=True, default=None)),
        migrations.AddField(model_name='realm', name='deletion_requested_at',
                            field=models.DateTimeField(null=True, blank=True, default=None)),
    ]
