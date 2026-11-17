from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("newsfeed", "0015_community_anonymity")]
    operations = [
        migrations.AddField(model_name="post", name="approval", field=models.PositiveSmallIntegerField(
            default=1, db_default=1, choices=[(0, "Pending"), (1, "Approved"), (2, "Unapproved")])),
        migrations.AddConstraint(model_name="post", constraint=models.CheckConstraint(
            condition=models.Q(approval__in=[0, 1, 2]), name="post_approval_valid")),
        migrations.AddIndex(model_name="post", index=models.Index(fields=["approval", "date_posted"],
            condition=~models.Q(approval=1), name="post_pending_approval_idx")),
    ]
