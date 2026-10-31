"""Shared Realm queryset annotations.

Kept in one place because the same "what is the acting entity to this realm"
question is answered by every realm listing and by the profile/manage payload,
and the answer has a non-obvious special case (see below) that must not drift
between them.
"""

from django.db.models import (
    BigIntegerField, Case, CharField, Count, Exists, Min, OuterRef, Subquery, Value, When,
)

from community.models import Member
from entity.permissions import MemberRole
from entity.models import Role


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
            Member.objects.filter(realm=OuterRef("pk"), entity=entity).values("role__name")[
                :1
            ]
        ),
        output_field=CharField(),
    )


def my_role_id_annotation(entity):
    """Exact role ID, including the realm owner's assigned definition for self access."""
    owners = Member.objects.filter(realm=OuterRef("pk"), role__name=MemberRole.OWNER)
    owner_role = (
        owners.order_by().values("realm")
        .annotate(role_count=Count("role_id", distinct=True), role_pk=Min("role_id"))
        .annotate(resolved_id=Case(
            When(role_count=1, then="role_pk"),
            default=Value(None),
            output_field=BigIntegerField(),
        ))
        .values("resolved_id")[:1]
    )
    self_role = Case(
        When(Exists(owners), then=Subquery(owner_role)),
        default=Subquery(
            Role.objects.filter(name=MemberRole.OWNER, is_default=True).values("pk")[:1]
        ), output_field=BigIntegerField(),
    )
    return Case(
        When(entity=entity, then=self_role),
        default=Subquery(
            Member.objects.filter(realm=OuterRef("pk"), entity=entity).values("role_id")[:1]
        ), output_field=BigIntegerField(),
    )
