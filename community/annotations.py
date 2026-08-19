"""Shared Realm queryset annotations.

Kept in one place because the same "what is the acting entity to this realm"
question is answered by every realm listing and by the profile/manage payload,
and the answer has a non-obvious special case (see below) that must not drift
between them.
"""

from django.db.models import Case, CharField, OuterRef, Subquery, Value, When

from community.models import Member
from entity.permissions import MemberRole


def my_role_annotation(entity):
    """The acting entity's Member.role in each Realm, or None if not a member.

    `owner` when acting AS the realm itself: a realm's own entity never has a
    Member row in its own realm, so the subquery would miss and report None for
    the one actor with the most authority. Both /realms/remove-user and
    /s/update-member-realm-role resolve that same case to owner tier before
    their role lookups - this keeps the payload agreeing with them.

    Anonymous callers (entity=None) get None: the FK comparison becomes
    IS NULL, which never matches a realm's non-null entity, and the subquery
    finds no member row.
    """
    return Case(
        When(entity=entity, then=Value(MemberRole.OWNER.value)),
        default=Subquery(
            Member.objects.filter(realm=OuterRef("pk"), entity=entity).values("role")[
                :1
            ]
        ),
        output_field=CharField(),
    )
