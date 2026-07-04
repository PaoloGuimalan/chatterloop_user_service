import csv
import os

from dateutil import parser as dateutil_parser
from django.db import connection, transaction

from ..models import Attachment, Entry, MapView, Mood, Tag

BACKUP_DIR = r"C:\Users\paulo\Documents\Backups\db_backups\postgres"
BACKUP_STAMP = "202607050001"


def csv_path(table):
    return os.path.join(BACKUP_DIR, f"{table}_{BACKUP_STAMP}.csv")


def read_rows(table):
    with open(csv_path(table), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_bool(value):
    return str(value).strip().lower() == "true"


def to_optional(value):
    value = (value or "").strip()
    return value or None


def to_datetime(value):
    value = to_optional(value)
    return dateutil_parser.parse(value) if value else None


def to_date(value):
    dt = to_datetime(value)
    return dt.date() if dt else None


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


def reset_sequence(model):
    table = model._meta.db_table
    quoted = connection.ops.quote_name(table)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {quoted}), 1), "
            f"(SELECT MAX(id) FROM {quoted}) IS NOT NULL)",
            [table],
        )


def import_moods():
    rows = read_rows("diary_mood")
    Mood.objects.bulk_create(
        [Mood(id=int(row["id"]), name=row["name"], emoji=row["emoji"]) for row in rows]
    )
    reset_sequence(Mood)
    print(f"Created {len(rows)} moods.")


def import_tags():
    rows = read_rows("diary_tag")
    Tag.objects.bulk_create(
        [Tag(id=int(row["id"]), name=row["name"]) for row in rows]
    )
    reset_sequence(Tag)
    print(f"Created {len(rows)} tags.")


def import_entries():
    rows = read_rows("diary_entry")
    entries = [
        Entry(
            id=row["id"],
            account_id=row["account_id"],
            title=row["title"],
            content=row["content"],
            entry_date=to_date(row["entry_date"]),
            mood_id=int(row["mood_id"]) if to_optional(row["mood_id"]) else None,
            is_private=to_bool(row["is_private"]),
            created_at=to_datetime(row["created_at"]),
            updated_at=to_datetime(row["updated_at"]),
        )
        for row in rows
    ]
    with DisableAutoTimestamp(
        (Entry, "created_at", "auto_now_add"),
        (Entry, "updated_at", "auto_now"),
    ):
        Entry.objects.bulk_create(entries)
    print(f"Created {len(entries)} entries.")


def import_entry_tags():
    rows = read_rows("diary_entry_tags")
    through = Entry.tags.through
    links = [
        through(entry_id=row["entry_id"], tag_id=int(row["tag_id"])) for row in rows
    ]
    through.objects.bulk_create(links)
    print(f"Created {len(links)} entry-tag links.")


def import_attachments():
    rows = read_rows("diary_attachment")
    attachments = [
        Attachment(
            id=row["id"],
            entry_id=row["entry_id"],
            file_id=to_optional(row["file_id"]),
            file_name=to_optional(row["file_name"]),
            file_type=to_optional(row["file_type"]),
            url=row["url"],
            created_at=to_datetime(row["created_at"]),
        )
        for row in rows
    ]
    with DisableAutoTimestamp((Attachment, "created_at", "auto_now_add")):
        Attachment.objects.bulk_create(attachments)
    print(f"Created {len(attachments)} attachments.")


def import_mapviews():
    rows = read_rows("diary_mapview")
    mapviews = [
        MapView(
            map_view_id=row["map_view_id"],
            entry_id=row["entry_id"],
            status=to_bool(row["status"]),
            is_stationary=to_bool(row["is_stationary"]),
            latitude=float(row["latitude"]) if to_optional(row["latitude"]) else None,
            longitude=float(row["longitude"])
            if to_optional(row["longitude"])
            else None,
        )
        for row in rows
    ]
    MapView.objects.bulk_create(mapviews)
    print(f"Created {len(mapviews)} map views.")


def run():
    if (
        Entry.objects.exists()
        or Tag.objects.exists()
        or Mood.objects.exists()
        or Attachment.objects.exists()
        or MapView.objects.exists()
    ):
        print(
            "Diary tables already contain data. Aborting to avoid a double import. "
            "Truncate them first if you really intend to re-run this."
        )
        return

    with transaction.atomic():
        import_moods()
        import_tags()
        import_entries()
        import_entry_tags()
        import_attachments()
        import_mapviews()
