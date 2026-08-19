from django.shortcuts import render
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import Follow, Realm, Member, Invite, generate_invite_token
from .serializers import (
    RealmSerializer,
    RealmMemberSerializer,
    FollowSerializer,
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
from community.annotations import my_role_annotation
from entity.permissions import Permission
from entity.services.permission_resolver import has_permission
from entity.utils import get_entity_display_username, resolve_entity_target
from entity.services.follows import (
    follow_entity,
    unfollow_entity,
    approve_follow_request,
    decline_follow_request,
)
from entity.services.realtime import publish_profile_relationship_update
from user.services.mongohelpers import NotificationService
from user.utils.blocking import is_blocked


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
                followers_count=Count(
                    "entity__followers",
                    # Accepted followers only - a pending follow request
                    # is not a follower yet and must not inflate the
                    # public count.
                    filter=Q(entity__followers__status=True),
                    distinct=True,
                ),
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
                    Follow.objects.filter(
                        followee=OuterRef("entity_id"),
                        follower=entity,
                        status=True,
                    )
                ),
                my_role=my_role_annotation(entity),
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
                followers_count=Count(
                    "entity__followers",
                    # Accepted followers only - a pending follow request
                    # is not a follower yet and must not inflate the
                    # public count.
                    filter=Q(entity__followers__status=True),
                    distinct=True,
                ),
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
                    Follow.objects.filter(
                        followee=OuterRef("entity_id"),
                        follower=entity,
                        status=True,
                    )
                ),
                my_role=my_role_annotation(entity),
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
                    followers_count=Count(
                    "entity__followers",
                    # Accepted followers only - a pending follow request
                    # is not a follower yet and must not inflate the
                    # public count.
                    filter=Q(entity__followers__status=True),
                    distinct=True,
                ),
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
                        Follow.objects.filter(
                            followee=OuterRef("entity_id"), follower=entity
                        )
                    ),
                    my_role=my_role_annotation(entity),
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

    @staticmethod
    def _target_id(request):
        """
        Follow targets any entity now. `realm_id` is read first so existing
        webapp/mobile calls are untouched; `entity_id`/`target_id` are the
        general aliases used when following a person.
        """
        return (
            request.data.get("realm_id")
            or request.data.get("entity_id")
            or request.data.get("target_id")
        )

    @staticmethod
    def _notify_new_follower(follower, followee, is_pending=False):
        """
        Persist a "started following you" notification and push the SSE that
        makes the client refresh - same two-step (Mongo write + Redis publish
        on `events_<entity id>`) every other notification site uses.

        A pending request notifies differently: it is ACTIONABLE, so it goes
        out with referenceStatus=False and type="follow_request" so the
        client renders Approve/Decline rather than a passive "new follower"
        line. referenceID stays the follower's entity id either way - that is
        what the approve/decline call addresses.

        Entity-generic on both ends: the follower may be a person or a page
        (get_entity_display_username resolves either), and so may the
        followee - a page's admins see it once switched to that page.

        Best-effort by design: the follow edge and its feed backfill are
        already committed by the time this runs, so a Mongo/Redis hiccup must
        not turn a successful follow into a 500.
        """
        try:
            if is_pending:
                details = (
                    f"{get_entity_display_username(follower)} "
                    "requested to follow you."
                )
            else:
                details = (
                    f"{get_entity_display_username(follower)} started following you."
                )

            service = NotificationService()
            service.add_notification(
                referenceID=str(follower.id),
                referenceStatus=not is_pending,
                toUserID=followee.id,
                fromUserID=follower.id,
                content_headline=(
                    "Follow Request" if is_pending else "New Follower"
                ),
                content_details=details,
                type="follow_request" if is_pending else "follow",
                isRead=False,
            )

            now = datetime.now()
            data = {
                "logType": None,
                "pod": "podless",
                "event": "notifications",
                "message": {
                    "status": True,
                    "auth": True,
                    "message": details,
                    "result": "",
                },
                "dateTime": now.isoformat(),
            }
            RedisPubSubClient.publish_json(f"events_{followee.id}", data)
        except Exception as err:
            print(f"Failed to send follow notification: {err}")

    def post(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            raw_target = self._target_id(request)
            followee = resolve_entity_target(raw_target)

            if followee is None:
                return Response(
                    {"status": False, "message": "Target not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if followee.id == entity.id:
                return Response(
                    {"status": False, "message": "You cannot follow yourself"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # follow_entity also seeds this follower's feed bucket with the
            # followee's recent posts - the feed is fan-out-on-write keyed on
            # the follow graph. Idempotent, so a double-tap neither raises on
            # the unique constraint nor re-backfills.
            #
            # Against a PRIVATE user profile it parks the edge as pending and
            # seeds nothing; is_pending is what turns the button into
            # "Requested" and keeps the profile shut until they approve.
            created, is_pending = follow_entity(entity, followee)

            # Notify the followee - but ONLY when a new edge was actually
            # created, so a double-tap (or a re-follow of someone already
            # followed) cannot spam them. Deliberately placed in the endpoint
            # rather than inside follow_entity(): the contact-request flow
            # auto-follows too (user/views.py), and that already sends its own
            # contact_request notification - notifying from the service would
            # double up there.
            #
            # type="follow" puts these in the Connections section of the
            # redesigned Notifications page (see NOTIF_CONNECTION_TYPES in
            # server/routes/users/index.js). referenceStatus=True because
            # there is nothing to act on; referenceID is the follower's entity
            # id, which is what a future "Follow back" button would address.
            if created:
                self._notify_new_follower(entity, followee, is_pending=is_pending)

            # The followee may be on the follower's profile - their follower
            # count, and any "follows you" indicator, just changed. Also fires
            # for a pending request, which is what surfaces it while they are
            # already looking at that profile.
            if created:
                publish_profile_relationship_update(
                    followee.id,
                    entity.id,
                    "follow_requested" if is_pending else "followed",
                )

            return Response(
                {
                    "status": True,
                    "is_pending": is_pending,
                    "message": (
                        f"Requested to follow {raw_target}"
                        if is_pending
                        else f"Followed {raw_target}"
                    ),
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request):
        """
        Followee-side approve/decline of a follow request.

        The ACTING entity is always the one being followed - you can only
        answer requests addressed to you, so the target here is the
        REQUESTER, not the profile. `action` mirrors the contact-request
        accept/reject header so the clients handle both the same way.

        IDEMPOTENT. Answering settles the notification whether or not a
        pending row was actually found, and re-answering succeeds instead of
        erroring. The notification and the follow row can legitimately fall
        out of step - accepting a CONTACT request auto-approves any pending
        follow (link_follows_for_connection), and a requester can cancel by
        unfollowing - which used to leave a permanently unanswerable
        notification: the 404 returned before the notification was touched,
        so its Confirm/Decline buttons came back on every refetch and could
        never be cleared.

        The notification is therefore settled FIRST, and the follow row is
        only mutated when there is genuinely something pending.
        """
        entity = self.request.entity

        try:
            raw_target = self._target_id(request)
            follower = resolve_entity_target(raw_target)
            action = (
                request.headers.get("action") or request.data.get("action") or "approve"
            ).lower()

            if follower is None:
                return Response(
                    {"status": False, "message": "Requester not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            is_decline = action in ("decline", "reject")

            # Settle the notification unconditionally. This is the part the
            # user actually sees, and it must clear even when the underlying
            # request is already gone. Scoped by type (a follow request's
            # referenceID is an entity id, not unique across types) and by
            # recipient (one requester may have open requests against several
            # profiles; answering here must not clear the others).
            NotificationService().update_reference_status_by_type(
                str(follower.id), "follow_request", True, to_user_id=entity.id
            )

            if is_decline:
                # Only ever removes a PENDING row - declining must not be
                # usable to drop an established follower.
                handled = decline_follow_request(follower, entity)
                message = "Follow request declined"

                # The requester's profile view of us still reads "Requested";
                # there is deliberately no notification for a decline, so this
                # is the only thing that corrects their button.
                if handled:
                    publish_profile_relationship_update(
                        follower.id, entity.id, "follow_request_declined"
                    )
            else:
                handled = approve_follow_request(follower, entity)
                message = "Follow request approved"

                if handled:
                    self._notify_request_approved(entity, follower)
                else:
                    message = "Follow request already settled"

            return Response(
                {
                    "status": True,
                    # False means the notification was stale - nothing was
                    # pending to act on. The call still succeeded.
                    "changed": handled,
                    "message": message,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @staticmethod
    def _notify_request_approved(followee, follower):
        """
        Tell the requester their pending follow went through - without it a
        private profile silently opens up and they never look again.
        """
        try:
            details = (
                f"{get_entity_display_username(followee)} approved your follow request."
            )

            service = NotificationService()
            service.add_notification(
                referenceID=str(followee.id),
                referenceStatus=True,
                toUserID=follower.id,
                fromUserID=followee.id,
                content_headline="Follow Request Approved",
                content_details=details,
                type="follow",
                isRead=False,
            )

            now = datetime.now()
            RedisPubSubClient.publish_json(
                f"events_{follower.id}",
                {
                    "logType": None,
                    "pod": "podless",
                    "event": "notifications",
                    "message": {
                        "status": True,
                        "auth": True,
                        "message": details,
                        "result": "",
                    },
                    "dateTime": now.isoformat(),
                },
            )

            # If they are still sitting on this profile, its button says
            # "Requested" and its feed is empty - both now wrong.
            publish_profile_relationship_update(
                follower.id, followee.id, "follow_request_approved"
            )
        except Exception as err:
            print(f"Failed to send follow approval notification: {err}")

    def delete(self, request):
        user = self.request.user
        entity = self.request.entity

        try:
            raw_target = self._target_id(request)
            followee = resolve_entity_target(raw_target)

            if followee is None:
                return Response(
                    {"status": False, "message": "Target not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Also pulls the followee's fanned-out posts back out of this
            # follower's feed bucket. Unfollowing something you do not follow
            # is a no-op, not a 500.
            #
            # Deliberately status-agnostic: this is also how the REQUESTER
            # cancels a follow request they no longer want pending, which is
            # the same gesture (un-tap the button) on the client.
            unfollow_entity(entity, followee)

            # Withdrawing leaves the followee holding a notification that
            # still offers Confirm/Decline for a request that no longer
            # exists. Settle it here so the buttons go away - the approve and
            # decline paths do the same, and this is the third way a request
            # can end.
            #
            # referenceID is the REQUESTER's entity id (us), scoped to this
            # recipient so it cannot clear our pending requests to anyone
            # else. Unconditional: unfollow_entity does not report whether the
            # row it removed was pending, and settling an already-settled
            # notification is a no-op.
            NotificationService().update_reference_status_by_type(
                str(entity.id), "follow_request", True, to_user_id=followee.id
            )

            # Mirrors the post path: whoever we just unfollowed (or withdrew a
            # request from) may have our profile open, where the follower
            # count and any pending-request row are now wrong.
            publish_profile_relationship_update(
                followee.id, entity.id, "unfollowed"
            )

            return Response(
                {"status": True, "message": f"Unfollowed {raw_target}"},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class JoinGroupRealmV2(APIView):
    """
    One-click join for PUBLIC GROUP realms - NEW endpoint for the redesigned
    Search page's group cards (versioned v2 because the existing membership
    entry points - InviteView's invite/request flows - stay untouched for
    the live mobile app).

    POST /api/realm/join/v2  {realm_id}

    Group chats have no join gate today beyond being public: membership is a
    community_member row, which the Node messaging side reads LIVE (its
    GetAllReceivers queries community_member by realm_id, and a group's
    conversationID IS its realm_id) - so creating the row here is the whole
    join. Deliberately group-only: servers/pages have their own entry flows,
    and channels/conferences/voice are not search-discoverable at all.

    Idempotent - re-joining reports already_member instead of erroring.
    Returns conversation_id (== realm_id) so the client can open
    /messages/<conversation_id> directly.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        entity = self.request.entity

        try:
            realm_id = request.data.get("realm_id")
            if not realm_id:
                return Response(
                    {"status": False, "message": "realm_id is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Member rows only ever represent personal accounts (see the
            # TopRealms annotation notes) - a page can't sit in a group chat.
            if getattr(entity, "type", None) != "user":
                return Response(
                    {"status": False, "message": "Only users can join groups"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            realm = Realm.objects.filter(
                Q(realm_id=realm_id) | Q(id=realm_id),
                is_active=True,
            ).first()
            if realm is None:
                return Response(
                    {"status": False, "message": "Group not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if realm.type != "group" or realm.is_private:
                return Response(
                    {"status": False, "message": "This realm cannot be joined here"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if is_blocked(entity, realm.entity):
                return Response(
                    {"status": False, "message": "You cannot join this group"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            _, created = Member.objects.get_or_create(
                entity=entity,
                realm=realm,
                defaults={
                    "added_by": entity,
                    "role": "member",
                    "date_joined": now(),
                },
            )

            return Response(
                {
                    "status": True,
                    "message": "Joined group" if created else "Already a member",
                    "result": {
                        "already_member": not created,
                        # A group's conversationID is its realm_id - what the
                        # client feeds straight into /messages/<id>.
                        "conversation_id": realm.realm_id,
                        "realm_id": realm.realm_id,
                    },
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
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


class FollowersView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            realm_id = request.query_params.get("realm_id")
            search = request.query_params.get("search", None)

            realm_followers_query_set = (
                Follow.objects.prefetch_related("follower")
                # Follow targets an entity now; a realm's entity is reachable
                # via Realm.entity's reverse accessor ("realms").
                #
                # status=True for consistency with every other follower
                # surface. A realm can never hold a pending follow (only a
                # private USER profile gates follows), so this changes
                # nothing today - it just means the list stays correct if
                # that ever stops being true.
                .filter(followee__realms__id=str(realm_id), status=True)
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

            serialized_result = FollowSerializer(paginated_queryset, many=True)
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

            unfollow_realm_queryset = Follow.objects.get(
                follow_id=follow_id, followee=realm.entity
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

            delete_query = Follow.objects.get(follow_id=follow_id)
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
