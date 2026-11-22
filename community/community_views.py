"""Community lifecycle, reusing realm roles, memberships and join requests."""

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from community.api_serializers import CommunityCreateSerializer
from community.models import Invite, Member, Realm
from community.access import lock_realm_mutation
from community.posts import community_metadata
from core.profile_paths import ProfilePathError, validate_profile_path
from entity.models import Entity
from entity.permissions import MemberRole, Permission
from entity.roles import get_default_role, role_tier, role_tier_expression
from entity.services.permission_resolver import has_permission


def _community_card(realm, viewer, *, include_capabilities=False):
    data = community_metadata(realm, viewer)
    data.update(
        description=realm.description,
        cover_photo=realm.cover_photo,
        type="community",
        member_count=getattr(realm, "member_count", None),
        my_role=realm.my_role,
        my_role_id=realm.my_role_id,
        request_pending=realm.request_pending,
    )
    if include_capabilities:
        from community.role_policy import feature_availability, realm_capabilities
        from entity.models import Role

        role = Role.objects.filter(pk=realm.my_role_id).values("name", "code").first()
        data.update(my_role_name=role["name"] if role else None,
                    my_role_code=role["code"] if role else None,
                    features=feature_availability(), capabilities=realm_capabilities(viewer, realm))
    return data


def _communities_for(viewer):
    from community.access import active_realms
    return active_realms(Realm.objects.filter(type="community", is_active=True)).annotate(
        joined=Exists(Member.objects.filter(realm=OuterRef("pk"), entity=viewer)),
        member_count=Count("member", distinct=True),
        my_role=Subquery(
            Member.objects.filter(realm=OuterRef("pk"), entity=viewer).annotate(
                structural_role=role_tier_expression(),
            ).values("structural_role")[
                :1
            ]
        ),
        my_role_id=Subquery(
            Member.objects.filter(realm=OuterRef("pk"), entity=viewer).values("role_id")[:1]
        ),
        request_pending=Exists(
            Invite.objects.filter(
                realm=OuterRef("pk"),
                target_entity=viewer,
                kind="request",
                status="pending",
            )
        ),
    )


def _notify_membership_changed(realm, entity):
    from community.views import InviteView

    def publish():
        view = InviteView()
        view._notify_members_changed(realm)
        view._notify_access_changed(str(entity.pk), realm)

    transaction.on_commit(publish)


class CommunitiesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from community.views import Pagination

        viewer = request.entity
        queryset = _communities_for(viewer)
        query = request.query_params.get("search", "").strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) | Q(slug__icontains=query)
            )
        if request.query_params.get("joined") == "true":
            queryset = queryset.filter(joined=True)
        tagged_entities = {
            value.strip()
            for value in request.query_params.get("tagged_entities", "").split(",")
            if value.strip()
        }
        if tagged_entities:
            matching_members = (
                Member.objects.filter(
                    realm=OuterRef("pk"), entity_id__in=tagged_entities
                )
                .values("realm_id")
                .annotate(total=Count("entity_id", distinct=True))
                .filter(total=len(tagged_entities))
            )
            queryset = queryset.filter(Exists(matching_members))
        paginator = Pagination()
        page = paginator.paginate_queryset(
            queryset.order_by("-created_at"), request, view=self
        )
        return paginator.get_paginated_response(
            [_community_card(realm, viewer) for realm in page]
        )

    def post(self, request):
        viewer = request.entity
        if not has_permission(viewer, Permission.REALM_SERVER_CREATE):
            raise PermissionDenied("You are not allowed to create a community")
        serializer = CommunityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        slug = fields.pop("slug")
        try:
            validate_profile_path(slug, "slug")
        except ProfilePathError as exc:
            return Response(exc.payload, status=400)
        with transaction.atomic():
            if viewer.type == 'realm':
                from community.access import effective_realm_access, lock_realm_scope
                from entity.services.acting import can_act_as_entity
                source = get_object_or_404(Realm, entity=viewer, type='page')
                locked_source = lock_realm_scope(source.pk, exclusive=False)[-1]
                if not effective_realm_access(locked_source).allowed or not can_act_as_entity(request.user, viewer):
                    raise PermissionDenied('You cannot create a community using this identity.')
            destination = Entity.objects.create(type="realm")
            realm = Realm.objects.create(
                entity=destination,
                created_by=viewer,
                type="community",
                slug=slug,
                **fields,
            )
            Member.objects.create(
                entity=viewer,
                realm=realm,
                added_by=viewer,
                role=get_default_role(MemberRole.OWNER, realm=realm),
                date_joined=now(),
            )
        return Response(
            {
                "status": True,
                "result": _community_card(
                    _communities_for(viewer).get(pk=realm.pk), viewer
                ),
            },
            status=201,
        )


class CommunityDetailView(APIView):
    permission_classes = [AllowAny]

    def get_authenticators(self):
        if not self.request.headers.get("x-access-token"):
            return []
        return super().get_authenticators()

    def get(self, request, realm_id):
        # Private metadata supports the join-request screen. Content and the
        # member roster retain their independent audience checks.
        viewer = getattr(request, "entity", None)
        realm = get_object_or_404(
            _communities_for(viewer),
            Q(pk=realm_id) | Q(slug=realm_id) | Q(entity_id=realm_id),
        )
        return Response({"status": True, "result": _community_card(realm, viewer, include_capabilities=True)})


class CommunityJoinView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, realm_id):
        viewer = request.entity
        if Realm.objects.filter(entity=viewer, type="community").exists():
            raise PermissionDenied("Communities cannot become members of any realm")
        with transaction.atomic():
            realm = get_object_or_404(
                Realm.objects.all(),
                pk=realm_id,
                type="community",
                is_active=True,
            )
            realm = lock_realm_mutation(realm, viewer)
            if Member.objects.filter(realm=realm, entity=viewer).exists():
                return Response({"status": True, "is_member": True})
            if realm.is_private:
                from community.views import InviteView

                return InviteView()._request_access(realm, request.user, viewer)
            member = Member.objects.create(
                entity=viewer, realm=realm, added_by=viewer, date_joined=now()
            )
            from community.admin_notifications import notify_member_joined

            transaction.on_commit(lambda: notify_member_joined(str(member.pk)))
            _notify_membership_changed(realm, viewer)
        return Response({"status": True, "is_member": True})


class CommunityLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, realm_id):
        with transaction.atomic():
            realm = get_object_or_404(
                Realm.objects.all(), pk=realm_id, type="community"
            )
            realm = lock_realm_mutation(realm, request.entity)
            member = (
                Member.objects.select_related("role")
                .filter(realm=realm, entity=request.entity).first()
            )
            cancel_only = request.data.get("cancel_request") is True
            if (
                not cancel_only
                and member is not None
                and role_tier(member.role) == MemberRole.OWNER
            ):
                raise ValidationError(
                    "Transfer ownership before leaving this community."
                )
            if not cancel_only and member is not None:
                member.delete()
                _notify_membership_changed(realm, request.entity)
            from community.invite_review import cancel_own_join_requests
            cancel_own_join_requests(request.entity, realm.pk)
        return Response(
            {
                "status": True,
                "is_member": cancel_only and member is not None,
                "request_pending": False,
            }
        )
