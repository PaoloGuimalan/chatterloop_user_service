"""Remove the historical global default before remapping roles per realm."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("community", "0011_role_references")]
    operations = [migrations.AlterField("member", "role", models.ForeignKey(
        to="entity.role", on_delete=django.db.models.deletion.PROTECT,
        related_name="memberships",
    ))]
