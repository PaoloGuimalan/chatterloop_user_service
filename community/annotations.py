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
from entity.roles import role_tier_expression, role_tier_filter


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
            Member.objects.filter(realm=OuterRef("pk"), entity=entity).annotate(
                structural_role=role_tier_expression(),
            ).values("structural_role")[
                :1
            ]
        ),
        output_field=CharField(),
    )


def my_role_id_annotation(entity):
    """Exact role ID, including the realm owner's assigned definition for self access."""
    self_role = Subquery(Role.objects.filter(realm_id=OuterRef("pk"), code="owner", is_default=True).values("pk")[:1])
    return Case(
        When(entity=entity, then=self_role),
        default=Subquery(
            Member.objects.filter(realm=OuterRef("pk"), role__realm_id=OuterRef("pk"), entity=entity).values("role_id")[:1]
        ), output_field=BigIntegerField(),
    )


def with_configuration_hints(queryset, request):
    """Two bounded permission EXISTS hints for directory cards, not a per-card resolver loop."""
    from entity.models import Entity
    from community.notification_permissions import permitted_members
    actor_id = getattr(getattr(request, 'entity', None), 'pk', None)
    personal_id = getattr(getattr(request, 'user', None), 'entity_id', None)
    queryset = queryset.annotate(_configuration_actor=Case(
        When(entity_id=actor_id, then=Value(str(personal_id) if personal_id else None)),
        default=Value(str(actor_id) if actor_id else None), output_field=Entity._meta.pk))
    return queryset.annotate(**{
        '_' + flag: Exists(permitted_members(OuterRef('pk'), [code]).filter(
            entity_id=OuterRef('_configuration_actor')))
        for flag, code in [('can_manage_roles', 'realm.roles.manage'),
                           ('can_manage_permissions', 'realm.permissions.manage')]
    })
