import uuid
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("community", "0012_role_default_identity"), ("entity", "0027_community_permission_catalog")]
    operations = [
        migrations.AlterField("invite", "status", models.CharField(max_length=150, default="pending", choices=[
            ("for_approval", "Awaiting approval"), ("rejected", "Rejected"), ("pending", "Pending"),
            ("accepted", "Accepted"), ("declined", "Declined"), ("revoked", "Revoked")])),
        migrations.AddIndex(model_name="invite", index=models.Index(
            fields=["realm", "kind", "status", "-created_at"], name="realm_invite_review_idx")),
        migrations.CreateModel(name="Approvals", fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("target_kind", models.CharField(max_length=10, choices=[("post", "Post"), ("invite", "Invite"), ("join", "Join")])),
            ("target_id", models.CharField(max_length=150)),
            ("action", models.CharField(max_length=16, choices=[(v, v.title()) for v in (
                "submit", "edit", "approve", "reject", "takedown", "revoke", "accept", "decline")])),
            ("previous_state", models.JSONField(default=dict)), ("new_state", models.JSONField(default=dict)),
            ("reason", models.TextField(blank=True, default="")),
            ("content_version", models.DateTimeField(null=True, blank=True)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
            ("delivery_state", models.JSONField(default=dict, blank=True)),
            ("realm", models.ForeignKey(to="community.realm", on_delete=django.db.models.deletion.PROTECT, related_name="review_decisions")),
            ("actor", models.ForeignKey(to="entity.entity", on_delete=django.db.models.deletion.PROTECT, related_name="realm_review_decisions")),
        ], options={"db_table": "community_approvals", "ordering": ["-created_at", "-id"], "indexes": [models.Index(
            fields=["realm", "target_kind", "target_id", "-created_at"], name="realm_review_target_idx"),
            models.Index(fields=["target_kind", "target_id", "-created_at"], name="approval_subject_idx"),
            models.Index(fields=["created_at", "id"], condition=models.Q(delivery_state__status="pending"), name="realm_review_delivery_idx")]}),
    ]
