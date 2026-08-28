"""
Rewrite interests_interest.normalized_name to the space-free key form.

    python manage.py runscript renormalize_interests --script-args dry-run
    python manage.py runscript renormalize_interests

Interest keys used to be `name.strip().lower()`, which left spaces in them, so
"news and culture" and "newsandculture" were two different interests. The key
now has whitespace removed entirely - see InterestManager.get_or_create_by_name
- and this brings existing rows in line with it.

`name` is only trimmed and run-collapsed. It stays readable; the key is the
thing that changes.

COLLISIONS ARE THE RISK, NOT THE REWRITE
----------------------------------------
normalized_name is UNIQUE. Two rows whose names differ only by spacing collapse
onto one key, and the second UPDATE would violate that constraint. Measured
before writing this: 84 of 346 rows contain spaces and NONE of them collide.

Rather than trust that measurement forever, this refuses to write if it finds a
collision, and names the rows involved - merging them is
`moderation_service`'s merge-duplicates, which knows how to move affinity,
grants and diary links safely. This script only ever rewrites keys.
"""

import re
from collections import defaultdict

from django.db import transaction

from interests.models import Interest

WHITESPACE_RUN = re.compile(r"\s+")


def _forms(raw_name):
    """(display name, key) - mirrors InterestManager.get_or_create_by_name."""
    cleaned = WHITESPACE_RUN.sub(" ", (raw_name or "").strip())
    return cleaned, WHITESPACE_RUN.sub("", cleaned).lower()


def run(*args):
    dry_run = "dry-run" in args

    rows = list(Interest.objects.all().only("id", "name", "normalized_name"))
    planned = []
    by_key = defaultdict(list)

    for interest in rows:
        name, key = _forms(interest.name)
        by_key[key].append(interest)
        if key != interest.normalized_name or name != interest.name:
            planned.append((interest, name, key))

    collisions = {
        key: group for key, group in by_key.items() if len(group) > 1
    }

    print(f"{len(rows)} interests, {len(planned)} need rewriting")

    if collisions:
        print()
        print(f"REFUSING TO WRITE - {len(collisions)} key collision(s):")
        for key, group in list(collisions.items())[:10]:
            names = ", ".join(repr(item.name) for item in group)
            print(f"   {key!r} <- {names}")
        print()
        print("Merge these first (moderation_service: main.py merge-duplicates),")
        print("which moves affinity, grants and diary links rather than losing them.")
        return

    for interest, name, key in planned[:15]:
        change = f"{interest.normalized_name!r} -> {key!r}"
        renamed = f"   name {interest.name!r} -> {name!r}" if name != interest.name else ""
        print(f"   {change}{renamed}")
    if len(planned) > 15:
        print(f"   (+{len(planned) - 15} more)")

    if dry_run:
        print()
        print("DRY RUN - nothing was written.")
        return

    with transaction.atomic():
        for interest, name, key in planned:
            interest.name = name
            interest.normalized_name = key
            interest.save(update_fields=["name", "normalized_name"])

    print()
    print(f"rewrote {len(planned)} interests")
