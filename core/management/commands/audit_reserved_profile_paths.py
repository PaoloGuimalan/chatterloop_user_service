import json
from django.core.management.base import BaseCommand, CommandError
from core.profile_paths import ProfilePathError, get_reserved_profile_paths, validate_profile_path


class Command(BaseCommand):
    help = "Report existing reserved/malformed entity paths without modifying any records."

    def add_arguments(self, parser):
        parser.add_argument("--fail-on-collision", action="store_true")

    def handle(self, *args, **options):
        from user.models import Account
        from community.models import Realm
        from bot.models import Bot

        collisions = []
        reserved_paths = get_reserved_profile_paths()
        for model, field in ((Account, "username"), (Realm, "slug"), (Bot, "handle")):
            for row in model.objects.values("pk", "entity_id", field).iterator():
                try:
                    validate_profile_path(row[field], field, reserved_paths=reserved_paths)
                except ProfilePathError as error:
                    collisions.append({"model": model._meta.label, "id": str(row["pk"]),
                                       "entityId": str(row["entity_id"]), "path": row[field],
                                       "code": error.payload["code"]})
        self.stdout.write(json.dumps({"count": len(collisions), "collisions": collisions}, indent=2))
        if options["fail_on_collision"] and collisions:
            raise CommandError("Existing profile path collisions require review; no data was changed.")
