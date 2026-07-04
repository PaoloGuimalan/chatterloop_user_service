import csv
import os
import uuid

from dateutil import parser as dateutil_parser
from django.db import transaction

from entity.models import Entity
from ..models import Account, Connection

BACKUP_DIR = r"C:\Users\paulo\Documents\Backups\db_backups\postgres"
BACKUP_STAMP = "202607050001"

ACCOUNT_CSV = os.path.join(BACKUP_DIR, f"user_account_{BACKUP_STAMP}.csv")
CONNECTION_CSV = os.path.join(BACKUP_DIR, f"user_connection_{BACKUP_STAMP}.csv")


def to_bool(value):
    return str(value).strip().lower() == "true"


def to_optional(value):
    value = (value or "").strip()
    return value or None


def to_datetime(value):
    value = to_optional(value)
    return dateutil_parser.parse(value) if value else None


class DisableAutoTimestamp:
    """Temporarily disable auto_now/auto_now_add so historical timestamps survive bulk_create."""

    def __init__(self, *fields):
        self.fields = fields
        self.saved = []

    def __enter__(self):
        for model, field_name, attr in self.fields:
            field = model._meta.get_field(field_name)
            self.saved.append((field, attr, getattr(field, attr)))
            setattr(field, attr, False)
        return self

    def __exit__(self, exc_type, exc, tb):
        for field, attr, original in self.saved:
            setattr(field, attr, original)


def import_accounts():
    with open(ACCOUNT_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    entities = []
    accounts = []
    account_map = {}

    for row in rows:
        entity = Entity(
            id=str(uuid.uuid4()),
            type="user",
            created_at=to_datetime(row["date_created"]),
        )
        account = Account(
            id=row["id"],
            entity=entity,
            username=row["username"],
            first_name=row["first_name"],
            middle_name=row["middle_name"] or "N/A",
            last_name=row["last_name"],
            birthdate=to_datetime(row["birthdate"]),
            profile=row["profile"] or "none",
            coverphoto=row["coverphoto"] or "none",
            gender=to_optional(row["gender"]),
            email=row["email"],
            password=row["password"],
            date_created=to_datetime(row["date_created"]),
            date_updated=to_datetime(row["date_updated"]),
            is_active=to_bool(row["is_active"]),
            is_verified=to_bool(row["is_verified"]),
            is_badged=to_bool(row["is_badged"]),
            is_default_user=to_bool(row["is_default_user"]),
            is_superuser=to_bool(row["is_superuser"]),
            user_type=row["user_type"],
            join_type=row["join_type"],
            connection_count=int(row["connection_count"]),
            ranking_score=float(row["ranking_score"]),
        )
        entities.append(entity)
        accounts.append(account)
        account_map[row["id"]] = entity

    with DisableAutoTimestamp(
        (Entity, "created_at", "auto_now_add"),
        (Account, "date_updated", "auto_now"),
    ):
        Entity.objects.bulk_create(entities)
        Account.objects.bulk_create(accounts)

    print(f"Created {len(entities)} entities and {len(accounts)} accounts.")
    return account_map


def import_connections(account_map):
    with open(CONNECTION_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    connections = []
    skipped = []

    for row in rows:
        action_by = account_map.get(row["action_by_id"])
        involved_entity = account_map.get(row["involved_user_id"])

        if action_by is None or involved_entity is None:
            skipped.append(row["id"])
            continue

        connections.append(
            Connection(
                id=row["id"],
                connection_id=row["connection_id"],
                action_by=action_by,
                nickname=to_optional(row["nickname"]),
                status=to_bool(row["status"]),
                involved_entity=involved_entity,
                action_date=to_datetime(row["action_date"]),
                type=row["type"],
                interaction_score=float(row["interaction_score"]),
                last_interaction_at=to_datetime(row["last_interaction_at"]),
            )
        )

    with DisableAutoTimestamp((Connection, "last_interaction_at", "auto_now")):
        Connection.objects.bulk_create(connections)

    print(f"Created {len(connections)} connections.")
    if skipped:
        print(f"Skipped {len(skipped)} connections with unresolved accounts: {skipped}")


def run():
    if Entity.objects.exists() or Account.objects.exists():
        print(
            "Entity or Account rows already exist in this database. "
            "Aborting to avoid a double import. Truncate those tables first if you "
            "really intend to re-run this."
        )
        return

    with transaction.atomic():
        account_map = import_accounts()
        import_connections(account_map)
