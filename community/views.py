from django.shortcuts import render
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import RealmFollow, Realm, Member, Invite, generate_invite_token
from .serializers import (
    RealmSerializer,
    RealmMemberSerializer,
    RealmFollowSerializer,
    InviteSerializer,
)
from django.shortcuts import get_object_or_404
from django.db.models import (
    Q,
    Exists,
    OuterRef,
    Count,
    ExpressionWrapper,
    BooleanField,
)
from django.db import transaction
from django.utils.timezone import now
from datetime import datetime
from user.models import Account
from user.utils.external_requests import emailer
from user_service.services.redis import RedisPubSubClient
from entity.permissions import Permission
from entity.services.permission_resolver import has_permission


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class TopRealms(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            search = request.query_params.get("search", None)
            type = request.query_params.get("type", None)

            top_realm_queryset = Realm.objects.annotate(
                followers_count=Count("followers", distinct=True),
                members=Count("member", distinct=True),
                # A page's own entity is never itself a Member row of its
                # realm (Member rows only ever represent personal accounts),
                # so once switched to act as a page, `entity` IS the realm's
                # own entity and the Member-based Exists() below would always
                # miss for that realm. `Q(entity=entity)` catches exactly
                # that self-administration case.
                is_admin=ExpressionWrapper(
                    Q(
                        Exists(
                            Member.objects.filter(
                                Q(Q(role="admin") | Q(role="owner")),
                                realm=OuterRef("pk"),
                                entity=entity,
                            )
                        )
                    )
                    | Q(entity=entity),
                    output_field=BooleanField(),
                ),
                is_member=ExpressionWrapper(
                    Q(Exists(Member.objects.filter(realm=OuterRef("pk"), entity=entity)))
                    | Q(entity=entity),
                    output_field=BooleanField(),
                ),
                is_follower=Exists(
                    RealmFollow.objects.filter(realm=OuterRef("pk"), follower=entity)
                ),
            ).filter(type=type, is_private=False)

            if search:
                top_realm_queryset = top_realm_queryset.filter(
                    Q(slug__icontains=search) | Q(name__icontains=search)
                )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                top_realm_queryset, request, view=self
            )

            serialized_result = RealmSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MyRealms(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            search = request.query_params.get("search", None)
            type = request.query_params.get("type", None)

            my_realm_queryset = Realm.objects.annotate(
                followers_count=Count("followers", distinct=True),
                members=Count("member", distinct=True),
                # See TopRealms.get() above: `Q(entity=entity)` covers acting
                # as a page whose own entity can never be a Member row of
                # its own realm.
                is_admin=ExpressionWrapper(
                    Q(
                        Exists(
                            Member.objects.filter(
                                Q(Q(role="admin") | Q(role="owner")),
                                realm=OuterRef("pk"),
                                entity=entity,
                            )
                        )
                    )
                    | Q(entity=entity),
                    output_field=BooleanField(),
                ),
                is_member=ExpressionWrapper(
                    Q(Exists(Member.objects.filter(realm=OuterRef("pk"), entity=entity)))
                    | Q(entity=entity),
                    output_field=BooleanField(),
                ),
                is_follower=Exists(
                    RealmFollow.objects.filter(realm=OuterRef("pk"), follower=entity)
                ),
            ).filter(is_member=True, type=type)

            if search:
                my_realm_queryset = my_realm_queryset.filter(
                    Q(slug__icontains=search) | Q(name__icontains=search)
                )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                my_realm_queryset, request, view=self
            )

            serialized_result = RealmSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            realm_id = request.data.get("realm_id")
            fields = request.data.get("fields")

            realm = get_object_or_404(Realm, realm_id=realm_id)

            if not has_permission(entity, Permission.REALM_UPDATE, realm=realm):
                return Response(
                    {
                        "status": False,
                        "message": "You are not authorized to update realm",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            if fields:
                Realm.objects.filter(realm_id=realm_id).update(**fields)

            return Response(
                {
                    "status": True,
                    "message": "Realm has been updated",
                    "reference": realm_id,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FollowRealmView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            search = request.query_params.get("search", None)
            type = request.query_params.get("type", None)

            followed_realm_queryset = (
                Realm.objects.annotate(
                    followers_count=Count("followers", distinct=True),
                    members=Count("member", distinct=True),
                    # See TopRealms.get() above: `Q(entity=entity)` covers
                    # acting as a page whose own entity can never be a
                    # Member row of its own realm.
                    is_admin=ExpressionWrapper(
                        Q(
                            Exists(
                                Member.objects.filter(
                                    Q(Q(role="admin") | Q(role="owner")),
                                    realm=OuterRef("pk"),
                                    entity=entity,
                                )
                            )
                        )
                        | Q(entity=entity),
                        output_field=BooleanField(),
                    ),
                    is_member=ExpressionWrapper(
                        Q(
                            Exists(
                                Member.objects.filter(
                                    realm=OuterRef("pk"), entity=entity
                                )
                            )
                        )
                        | Q(entity=entity),
                        output_field=BooleanField(),
                    ),
                    is_follower=Exists(
                        RealmFollow.objects.filter(
                            realm=OuterRef("pk"), follower=entity
                        )
                    ),
                )
                .filter(is_follower=True, type=type)
                .order_by("-id")
            )

            if search:
                followed_realm_queryset = followed_realm_queryset.filter(
                    Q(slug__icontains=search) | Q(name__icontains=search)
                )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                followed_realm_queryset, request, view=self
            )

            serialized_result = RealmSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            realm_id = request.data.get("realm_id")

            realm = get_object_or_404(Realm, id=realm_id)
            follow_realm_queryset = RealmFollow.objects.create(
                follower=entity, realm=realm
            )

            if follow_realm_queryset is None:
                return Response(
                    {"status": False, "message": "Error completing follow request"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {"status": True, "message": f"Followed {realm_id}"},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            realm_id = request.data.get("realm_id")

            realm = get_object_or_404(Realm, id=realm_id)
            unfollow_realm_queryset = RealmFollow.objects.get(
                follower=entity, realm=realm
            )

            if unfollow_realm_queryset is None:
                return Response(
                    {"status": False, "message": "Error completing follow request"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            unfollow_realm_queryset.delete()

            return Response(
                {"status": True, "message": f"Followed {realm_id}"},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RealmMembersView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            realm_id = request.query_params.get("realm_id")
            search = request.query_params.get("search", None)

            realm = get_object_or_404(Realm, id=realm_id)

            if not has_permission(entity, Permission.REALM_MEMBER_VIEW, realm=realm):
                return Response(
                    {
                        "status": False,
                        "message": "You are not allowed to access members",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            realm_members_query_set = (
                Member.objects.prefetch_related("entity", "added_by")
                .filter(realm__id=realm_id)
                .order_by("-date_joined")
            )

            if search:
                if search.startswith("@"):
                    realm_members_query_set = realm_members_query_set.filter(
                        Q(entity__users__username__icontains=search)
                    )
                else:
                    realm_members_query_set = realm_members_query_set.filter(
                        Q(
                            Q(entity__users__first_name__icontains=search)
                            | Q(entity__users__middle_name__icontains=search)
                            | Q(entity__users__last_name__icontains=search)
                        )
                    )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                realm_members_query_set, request, view=self
            )

            serialized_result = RealmMemberSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RealmFollowersView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            realm_id = request.query_params.get("realm_id")
            search = request.query_params.get("search", None)

            realm_followers_query_set = (
                RealmFollow.objects.prefetch_related("follower")
                .filter(realm__id=str(realm_id))
                .order_by("-created_at")
            )

            if search:
                if search.startswith("@"):
                    realm_followers_query_set = realm_followers_query_set.filter(
                        Q(follower__users__username__icontains=search)
                    )
                else:
                    realm_followers_query_set = realm_followers_query_set.filter(
                        Q(
                            Q(follower__users__first_name__icontains=search)
                            | Q(follower__users__middle_name__icontains=search)
                            | Q(follower__users__last_name__icontains=search)
                        )
                    )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                realm_followers_query_set, request, view=self
            )

            serialized_result = RealmFollowSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            realm_id = request.data.get("realm_id")
            follow_id = request.data.get("follow_id")

            realm = get_object_or_404(Realm, id=realm_id)

            if not has_permission(
                entity, Permission.REALM_FOLLOWER_REMOVE, realm=realm
            ):
                return Response(
                    {
                        "status": False,
                        "message": "You are not allowed to remove follower",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            unfollow_realm_queryset = RealmFollow.objects.get(
                follow_id=follow_id, realm=realm
            )

            if unfollow_realm_queryset is None:
                return Response(
                    {"status": False, "message": "Error completing follow request"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            unfollow_realm_queryset.delete()

            return Response(
                {"status": True, "message": f"Followed {realm_id}"},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InviteView(APIView):
    permission_classes = [IsAuthenticated]

    def _serialize_invite(self, invite):
        return InviteSerializer(invite).data

    def _publish_event(self, entity_id, event, message):
        if not entity_id:
            return
        try:
            RedisPubSubClient.publish_json(
                f"events_{entity_id}",
                {
                    "logType": None,
                    "pod": "podless",
                    "event": event,
                    "message": message,
                    "dateTime": datetime.now().isoformat(),
                },
            )
        except Exception as err:
            print(f"Failed to publish {event} event: {err}")

    # The conference SSE events below are intentionally payload-light: they
    # only signal "this list changed, refetch it" with the realm_id. The
    # client always pulls the full list from the REST endpoints, so realtime
    # never has to carry (and risk desyncing) the actual request/member rows.
    def _realm_admin_recipient_ids(self, realm):
        recipient_ids = set(
            Member.objects.filter(realm=realm, role="admin").values_list(
                "entity__id", flat=True
            )
        )
        if realm.created_by_id:
            recipient_ids.add(realm.created_by_id)
        return recipient_ids

    def _notify_requests_changed(self, realm):
        message = {"status": True, "auth": True, "realm_id": realm.realm_id}
        for recipient_id in self._realm_admin_recipient_ids(realm):
            self._publish_event(recipient_id, "conference_requests_changed", message)

    def _notify_members_changed(self, realm):
        recipient_ids = set(
            Member.objects.filter(realm=realm).values_list("entity__id", flat=True)
        )
        if realm.created_by_id:
            recipient_ids.add(realm.created_by_id)
        message = {"status": True, "auth": True, "realm_id": realm.realm_id}
        for recipient_id in recipient_ids:
            self._publish_event(recipient_id, "conference_members_changed", message)

    def _notify_access_changed(self, entity_id, realm):
        # Targeted signal so a single requester refetches their room/access info.
        if not entity_id:
            return
        message = {"status": True, "auth": True, "realm_id": realm.realm_id}
        self._publish_event(entity_id, "conference_access_changed", message)

    def _add_member_if_missing(self, invite, actor):
        # For email invites target_entity is often unset (only target_email is
        # known), so fall back to the user accepting the invite. For requests,
        # target_entity (the requester) is set and rightly takes precedence over
        # the admin acting on it. Without this, accepted invitees never become
        # members and are forced to re-request access on every rejoin.
        entity = invite.target_entity or actor
        if not entity:
            return

        Member.objects.get_or_create(
            entity=entity,
            realm=invite.realm,
            defaults={
                "added_by": actor,
                "role": "member",
                "date_joined": now(),
            },
        )

    def post(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            realm_id = request.data.get("realm_id")
            target_email = request.data.get("target_email")
            kind = request.data.get("kind", "invite")

            if not realm_id or not target_email:
                return Response(
                    {
                        "status": False,
                        "message": "realm_id and target_email are required",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            realm = get_object_or_404(Realm, realm_id=realm_id)
            normalized_kind = "request" if kind == "request" else "invite"

            if normalized_kind != "request" and not has_permission(
                entity, Permission.REALM_INVITE_CREATE, realm=realm
            ):
                return Response(
                    {"status": False, "message": "You are not allowed to invite users"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            normalized_email = str(target_email).strip().lower()
            target_entity = Account.objects.filter(
                email__iexact=normalized_email
            ).first()

            existing_pending_invite = (
                Invite.objects.filter(
                    realm=realm,
                    target_email=normalized_email,
                    status="pending",
                )
                .order_by("-created_at")
                .first()
            )

            if existing_pending_invite:
                existing_pending_invite.kind = normalized_kind
                existing_pending_invite.target_entity = target_entity.entity
                existing_pending_invite.created_by = entity
                existing_pending_invite.created_at = now()
                existing_pending_invite.resolved_at = None
                if normalized_kind == "invite":
                    existing_pending_invite.invite_token = generate_invite_token()
                existing_pending_invite.save()
                invite = existing_pending_invite
            else:
                invite = Invite.objects.create(
                    realm=realm,
                    kind=normalized_kind,
                    status="pending",
                    target_email=normalized_email,
                    target_entity=target_entity.entity,
                    created_by=entity,
                )

            if normalized_kind == "invite":
                frontend_base_url = getattr(
                    settings, "FRONTEND_URL", "https://chatterloop.app"
                ).rstrip("/")
                invite_path = (
                    f"/conference/{realm.slug}"
                    if realm.type == "conference" and realm.slug
                    else f"/{realm.slug or realm.realm_id}"
                )
                invite_link = f"{frontend_base_url}{invite_path}?invite_token={invite.invite_token}"
                emailer.send_realm_invite_email(
                    to_email=normalized_email,
                    realm_name=realm.name,
                    invite_link=invite_link,
                    inviter_name=user.username,
                )
            else:
                self._notify_requests_changed(realm)

            return Response(
                {
                    "status": True,
                    "message": (
                        "Invite created"
                        if normalized_kind == "invite"
                        else "Access request submitted"
                    ),
                    "result": self._serialize_invite(invite),
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def get(self, request):
        try:
            invite_token = request.query_params.get("invite_token")
            realm_id = request.query_params.get("realm_id")
            target_email = request.query_params.get("target_email")

            if invite_token:
                invite = get_object_or_404(Invite, invite_token=invite_token)
                return Response(
                    {
                        "status": True,
                        "result": self._serialize_invite(invite),
                    },
                    status=status.HTTP_200_OK,
                )

            if realm_id and target_email:
                invite = (
                    Invite.objects.filter(
                        realm__realm_id=realm_id,
                        target_email=str(target_email).strip().lower(),
                    )
                    .order_by("-created_at")
                    .first()
                )

                if not invite:
                    return Response(
                        {"status": False, "message": "Invite not found"},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                return Response(
                    {
                        "status": True,
                        "result": self._serialize_invite(invite),
                    },
                    status=status.HTTP_200_OK,
                )

            if realm_id:
                realm = get_object_or_404(Realm, realm_id=realm_id)

                if not has_permission(
                    request.entity, Permission.REALM_MEMBER_VIEW, realm=realm
                ):
                    return Response(
                        {
                            "status": False,
                            "message": "You are not allowed to view invites for this realm",
                        },
                        status=status.HTTP_401_UNAUTHORIZED,
                    )

                invites = Invite.objects.filter(realm=realm)

                kind = request.query_params.get("kind")
                if kind in {"invite", "request"}:
                    invites = invites.filter(kind=kind)

                status_filter = request.query_params.get("status")
                if status_filter in {"pending", "accepted", "declined", "revoked"}:
                    invites = invites.filter(status=status_filter)

                invites = invites.order_by("-created_at")
                return Response(
                    {
                        "status": True,
                        "result": InviteSerializer(invites, many=True).data,
                    },
                    status=status.HTTP_200_OK,
                )

            invites = Invite.objects.filter(created_by=request.user).order_by(
                "-created_at"
            )
            return Response(
                {
                    "status": True,
                    "result": InviteSerializer(invites, many=True).data,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def patch(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            invite_token = request.data.get("invite_token")
            status_value = request.data.get("status")

            if not invite_token or not status_value:
                return Response(
                    {
                        "status": False,
                        "message": "invite_token and status are required",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            invite = get_object_or_404(Invite, invite_token=invite_token)
            normalized_status = str(status_value).strip().lower()

            if normalized_status not in {"accepted", "declined", "revoked"}:
                return Response(
                    {
                        "status": False,
                        "message": "Unsupported invite status",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if normalized_status == "accepted":
                if invite.kind == "request":
                    # A host/admin approves another member's join request.
                    if not has_permission(
                        entity, Permission.REALM_REQUEST_APPROVE, realm=invite.realm
                    ):
                        return Response(
                            {
                                "status": False,
                                "message": "You are not allowed to approve this request",
                            },
                            status=status.HTTP_401_UNAUTHORIZED,
                        )
                else:
                    if (
                        invite.target_email
                        and invite.target_email != user.email.lower()
                    ):
                        return Response(
                            {
                                "status": False,
                                "message": "This invite is not assigned to your account",
                            },
                            status=status.HTTP_401_UNAUTHORIZED,
                        )

                    if invite.target_entity and invite.target_entity != entity:
                        return Response(
                            {
                                "status": False,
                                "message": "This invite was assigned to another account",
                            },
                            status=status.HTTP_401_UNAUTHORIZED,
                        )

            if normalized_status == "declined" and invite.kind == "request":
                # A host/admin can decline an incoming join request, or the
                # requester can withdraw their own request - the latter is a
                # resource-identity check, not a grantable permission.
                if (
                    not has_permission(
                        entity, Permission.REALM_REQUEST_DECLINE, realm=invite.realm
                    )
                    and invite.created_by_id != entity.id
                ):
                    return Response(
                        {
                            "status": False,
                            "message": "You are not allowed to decline this request",
                        },
                        status=status.HTTP_401_UNAUTHORIZED,
                    )

            if normalized_status == "revoked":
                if (
                    not has_permission(
                        entity, Permission.REALM_INVITE_REVOKE, realm=invite.realm
                    )
                    and invite.created_by_id != entity.id
                ):
                    return Response(
                        {
                            "status": False,
                            "message": "You are not allowed to revoke this invite",
                        },
                        status=status.HTTP_401_UNAUTHORIZED,
                    )

            with transaction.atomic():
                invite.status = normalized_status
                invite.resolved_at = now()
                if normalized_status == "accepted":
                    invite.accepted_by_entity = entity
                invite.save()

                if normalized_status == "accepted":
                    self._add_member_if_missing(invite, entity)

            # Accepting adds a member, so the participants list changed.
            if normalized_status == "accepted":
                self._notify_members_changed(invite.realm)

            # A resolved join request changes the host's pending list, and the
            # requester needs to refetch their access/room info either way.
            if invite.kind == "request" and normalized_status in {
                "accepted",
                "declined",
            }:
                self._notify_requests_changed(invite.realm)
                self._notify_access_changed(invite.target_entity_id, invite.realm)

            return Response(
                {
                    "status": True,
                    "message": f"Invite {normalized_status}",
                    "result": self._serialize_invite(invite),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            realm_id = request.data.get("realm_id")
            follow_id = request.data.get("follow_id")

            realm = get_object_or_404(Realm, id=realm_id)

            if not has_permission(
                entity, Permission.REALM_FOLLOWER_REMOVE, realm=realm
            ):
                return Response(
                    {
                        "status": False,
                        "message": "You are not allowed to remove follower",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            delete_query = RealmFollow.objects.get(follow_id=follow_id)
            delete_query.delete()

            return Response(
                {
                    "status": True,
                    "message": "Follower has been removed",
                    "reference": follow_id,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
