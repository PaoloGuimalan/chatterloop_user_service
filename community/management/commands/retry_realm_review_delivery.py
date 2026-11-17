from django.core.management.base import BaseCommand, CommandError
from community.models import Approvals
from community.review_delivery import publish_review_effects
from community.invite_review import deliver_reviewed_invitation, notify_invite_review_activity


class Command(BaseCommand):
    help = "Retry a bounded batch of committed community post review delivery."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 1000:
            raise CommandError("Limit must be between 1 and 1000.")
        rows = list(Approvals.objects.filter(target_kind__in=["post", "invite"], delivery_state__status="pending")
                   .order_by("created_at", "pk").values("pk", "target_kind", "action")[:limit])
        delivered = 0
        for row in rows:
            decision_id = row["pk"]
            try:
                deliver = publish_review_effects if row["target_kind"] == "post" else (
                    notify_invite_review_activity if row["action"] == "submit" else deliver_reviewed_invitation)
                delivered += bool(deliver(decision_id))
            except Exception as exc:
                self.stderr.write(f"Delivery remains pending for {decision_id}: {type(exc).__name__}")
        self.stdout.write(f"Processed {len(rows)} decisions; completed {delivered}.")
