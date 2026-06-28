"""Backfill one Entity row per existing Account and Realm.

Idempotent and batched. Must be run (and verified) before the Phase C data
migration that populates ``actor_entity`` foreign keys, since those FKs must
already exist.

Usage::

    python manage.py backfill_entities            # apply
    python manage.py backfill_entities --dry-run  # report only
    python manage.py backfill_entities --batch-size 1000
"""

from django.core.management.base import BaseCommand

from community.models import Realm
from user.models import Account

from entity.models import Entity, build_entity_id


class Command(BaseCommand):
    help = "Create an Entity for every existing Account and Realm (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created without writing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=2000,
            help="Rows per bulk_create batch (default 2000).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        existing_ids = set(Entity.objects.values_list("entity_id", flat=True))

        accounts = self._backfill(
            queryset=Account.objects.all().only("id"),
            entity_type=Entity.ENTITY_TYPE_USER,
            source_type=Entity.SOURCE_TYPE_ACCOUNT,
            source_id_attr="id",
            existing_ids=existing_ids,
            batch_size=batch_size,
            dry_run=dry_run,
        )
        realms = self._backfill(
            queryset=Realm.objects.all().only("realm_id"),
            entity_type=Entity.ENTITY_TYPE_REALM,
            source_type=Entity.SOURCE_TYPE_REALM,
            source_id_attr="realm_id",
            existing_ids=existing_ids,
            batch_size=batch_size,
            dry_run=dry_run,
        )

        verb = "Would create" if dry_run else "Created"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {accounts} user entities and {realms} realm entities."
            )
        )

    def _backfill(
        self,
        queryset,
        entity_type,
        source_type,
        source_id_attr,
        existing_ids,
        batch_size,
        dry_run,
    ):
        created = 0
        pending = []
        for obj in queryset.iterator(chunk_size=batch_size):
            source_id = str(getattr(obj, source_id_attr))
            entity_id = build_entity_id(entity_type, source_id)
            if entity_id in existing_ids:
                continue
            existing_ids.add(entity_id)
            created += 1
            if dry_run:
                continue
            pending.append(
                Entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    source_type=source_type,
                    source_id=source_id,
                )
            )
            if len(pending) >= batch_size:
                Entity.objects.bulk_create(pending, ignore_conflicts=True)
                pending = []
        if pending:
            Entity.objects.bulk_create(pending, ignore_conflicts=True)
        return created
