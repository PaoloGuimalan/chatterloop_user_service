"""
Apply interests/taxonomy.py to the database.

    python manage.py runscript seed_taxonomy --script-args dry-run
    python manage.py runscript seed_taxonomy

Idempotent and strictly ADDITIVE - safe to re-run after editing the taxonomy.
It creates missing rows and fills in missing parents. It never renames, never
deletes, and never re-points a parent that is already set, so a hand-made
correction in the admin survives the next run.

Every existing Interest carries EntityInterestAffinity and diary EntryTagLink
rows, so adoption is free: setting a parent moves no data and breaks no link.

DRY RUN DOES REAL WRITES, THEN ROLLS BACK
-----------------------------------------
The first version of this skipped the writes and reported from a read-only
walk, which lied: with no categories actually created, nothing could be
parented to them, so it reported 9 adoptions where the real run does 55. A
rehearsal that takes a different code path is not a rehearsal. This one runs the
identical path inside a transaction and rolls it back, so the numbers printed
are the numbers you get.
"""

from django.db import transaction

from interests.models import Interest
from interests.taxonomy import LEAVE_ORPHANED, REPARENT, SEED


class Report:
    def __init__(self):
        self.created = []
        self.parented = []
        self.already_parented = 0
        self.missing = []
        self.orphans = []


def _adopt(interest, parent, report):
    """Set a parent only when there is not one already.

    Never re-points: a parent set by hand in the admin is a deliberate
    correction, and a re-run must not undo it.
    """
    if interest.parent_id is not None:
        report.already_parented += 1
        return
    if interest.pk == parent.pk:
        return

    interest.parent = parent
    interest.save(update_fields=["parent"])
    report.parented.append((interest.name, parent.name))


def _apply(report):
    # get_or_create_by_name, not objects.create: normalisation has to be
    # Django's own, or a row keyed differently here becomes a duplicate that
    # nothing else can ever find.
    categories = {}
    for category_name in SEED:
        category, created = Interest.objects.get_or_create_by_name(category_name)
        categories[category_name] = category
        if created:
            report.created.append(category_name)

    for category_name, children in SEED.items():
        parent = categories[category_name]
        for child_name in children:
            child, created = Interest.objects.get_or_create_by_name(child_name)
            if created:
                report.created.append(child_name)
            _adopt(child, parent, report)

    for existing_name, category_name in REPARENT.items():
        interest = Interest.objects.filter(
            normalized_name=existing_name.strip().lower()
        ).first()
        if interest is None:
            # Not an error - the taxonomy is shared across environments and a
            # row named here may simply not exist in this one.
            report.missing.append(existing_name)
            continue
        _adopt(interest, categories[category_name], report)

    # Excluded by NORMALISED name, not by name: a pre-existing row can be
    # adopted as a category under different casing ("sports" becoming the
    # "Sports" category), and matching on the raw name reported it as a
    # stray root when it is the category itself.
    category_keys = {name.strip().lower() for name in SEED}
    report.orphans = sorted(
        Interest.objects.filter(parent__isnull=True)
        .exclude(normalized_name__in=category_keys)
        .values_list("name", flat=True)
    )


def run(*args):
    dry_run = "dry-run" in args
    report = Report()

    with transaction.atomic():
        _apply(report)
        if dry_run:
            transaction.set_rollback(True)

    verb = "would create" if dry_run else "created"
    print(f"{verb:<15} {len(report.created)} interests")
    if report.created:
        preview = ", ".join(report.created[:6])
        more = f" (+{len(report.created) - 6} more)" if len(report.created) > 6 else ""
        print(f"                {preview}{more}")

    verb = "would parent" if dry_run else "parented"
    print(f"{verb:<15} {len(report.parented)} interests")
    for name, category in report.parented[:10]:
        print(f"                {name} -> {category}")
    if len(report.parented) > 10:
        print(f"                (+{len(report.parented) - 10} more)")

    if report.already_parented:
        print(f"{'unchanged':<15} {report.already_parented} already had a parent")

    if report.missing:
        print(f"{'not in this db':<15} {len(report.missing)} REPARENT names absent")
        print(f"                {', '.join(report.missing[:8])}")

    unexpected = [name for name in report.orphans if name not in LEAVE_ORPHANED]
    print()
    print(f"{'orphan roots':<15} {len(report.orphans)}")
    print(f"{'  expected':<15} {len(LEAVE_ORPHANED)} listed in LEAVE_ORPHANED")
    if unexpected:
        print(f"{'  UNEXPECTED':<15} {len(unexpected)}: {', '.join(unexpected[:12])}")
    else:
        print(f"{'  unexpected':<15} none")

    if dry_run:
        print()
        print("DRY RUN - transaction rolled back, nothing was written.")
