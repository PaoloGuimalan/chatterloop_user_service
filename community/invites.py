"""
Realm invites: by email or by username, into a group, a server (with its
channels), a conference, or a page - to join its team or to follow it.

WHO HEARS ABOUT AN INVITE
-------------------------
- Someone with an account (found by username, or by the email they signed up
  with) gets an in-app notification with Accept / Decline, a push, and the
  live "notifications" event.
- An email address is also EMAILED a link to the invite's page - whether or
  not an account uses it, because the person typed an email.
- An email with no account yet waits as a pending invite. Signing up with that
  address (once the address is proven - see claim_pending_invites) turns it
  into the in-app invite above.

WHAT ACCEPTING DOES
-------------------
All of it here, against the shared database - Django and Node never call each
other. Membership of a group or a server is a community_member row, which the
Node messaging side reads live; a server join mirrors Node's own
/s/addnewmembertoserver (the server, every channel under it, and the
realm_membership_changed event to everyone in it). The "maya joined" line in
the chat is the worker's (post_conversation_notice): it also puts the new
member in the conversation's participants, which is what the chat list and
message pushes are keyed on.
"""

import logging
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils.timezone import now

from community.invite_rules import (
    InviteError,
    invite_notification_ux,
    invite_route,
    invite_sentence,
    parse_target,
    resolve_purpose,
)
from community.models import Invite, Member, Realm
from entity.models import Entity
from entity.permissions import Permission
from entity.services.follows import accepted_follow_exists, follow_entity
from entity.services.permission_resolver import has_permission
from entity.utils import get_entity_display_username, get_entity_profile_path
from user.models import Account
from user.services.mongohelpers import NotificationService
from user.utils.blocking import is_blocked
from user.utils.external_requests import emailer
from user_service.services import push
from user_service.services.rabbitmq import Queues, RabbitMQClient
from user_service.services.redis import RedisPubSubClient

logger = logging.getLogger(__name__)

INVITE_NOTIFICATION_TYPE = "realm_invite"


# --- who may invite -------------------------------------------------------


def _is_member(entity, realm):
    return Member.objects.filter(entity=entity, realm=realm).exists()


def _member_role(entity, realm):
    member = Member.objects.filter(entity=entity, realm=realm).first()
    return member.role if member else None


def _acts_as_realm(entity, realm):
    """The realm's own entity (a page switched to itself), or its creator."""
    return str(entity.id) in (str(realm.entity_id), str(realm.created_by_id))


def assert_can_invite(entity, realm, purpose, role):
    """
    Raises InviteError unless `entity` may send this invite.

    Mirrors who can ADD people today, so invites take nothing away:
      group, server - any member (Node's /m/addnewmember and
                      /s/addnewmembertoserver check membership only)
      conference    - the realm.invite.create permission, as before
      page          - realm.invite.create (owner, admins, the page itself);
                      and an ADMIN invite only from the owner or the page,
                      because an admin cannot make another admin (the
                      target-role rule in entity/permissions.py)
    """
    if realm.type in ("group", "server"):
        if _acts_as_realm(entity, realm) or _is_member(entity, realm):
            return
        raise InviteError("Only members can invite people here", 403)

    if not has_permission(entity, Permission.REALM_INVITE_CREATE, realm=realm):
        raise InviteError("You are not allowed to invite people here", 403)

    if purpose == "manage" and role == "admin":
        if not (_acts_as_realm(entity, realm) or _member_role(entity, realm) == "owner"):
            raise InviteError("Only the owner can invite another admin", 403)


# --- the target -----------------------------------------------------------


def resolve_target(raw_target=None, target_email=None, target_entity_id=None):
    """
    (entity or None, email or None) for whoever is being invited.

    Three ways in: someone picked from contacts or search (target_entity_id),
    what was typed into the box (raw_target: an email or a username), or the
    older target_email field. A username must exist; an email need not.

    A picked PAGE can be invited too - the direct add it replaces took pages -
    and its team answers when switched to it. A bot cannot: nobody acts as a
    bot to accept anything.
    """
    if target_entity_id:
        entity = Entity.objects.filter(id=target_entity_id).first()
        if entity is None:
            raise InviteError("That person couldn't be found", 404)
        if entity.type == "bot":
            raise InviteError("Bots can't accept invites", 400)
        if entity.type == "user" and not Account.objects.filter(
            entity_id=entity.id, is_active=True
        ).exists():
            raise InviteError("That person couldn't be found", 404)
        return entity, None

    if raw_target is None and target_email:
        raw_target = target_email

    kind, value = parse_target(raw_target)
    if kind == "email":
        account = (
            Account.objects.select_related("entity")
            .filter(email__iexact=value, is_active=True)
            .first()
        )
        return (account.entity if account else None), value

    account = (
        Account.objects.select_related("entity")
        .filter(username__iexact=value, is_active=True)
        .first()
    )
    if account is None:
        raise InviteError(f"No one goes by @{value}", 404)
    return account.entity, None


def _assert_not_already_in(target_entity, realm, purpose):
    if purpose == "follow":
        if accepted_follow_exists(target_entity, realm.entity):
            raise InviteError("They already follow this page", 409)
        return
    if _is_member(target_entity, realm) or str(target_entity.id) == str(
        realm.entity_id
    ):
        raise InviteError(
            "They're already on this page's team"
            if purpose == "manage"
            else "They're already a member",
            409,
        )


# --- creating -------------------------------------------------------------


def create_invite(
    realm,
    inviter,
    *,
    raw_target=None,
    target_email=None,
    target_entity_id=None,
    purpose=None,
    role=None,
):
    """
    Create (or find) the invite and tell the invitee. Returns (invite, created).

    An invite already pending for the same person, realm and purpose is
    returned as it is rather than sent again - repeating it would only stack
    notifications, and each would carry its own token.
    """
    purpose, role = resolve_purpose(realm.type, purpose, role)
    assert_can_invite(inviter, realm, purpose, role)

    target_entity, email = resolve_target(raw_target, target_email, target_entity_id)

    if target_entity is not None:
        if str(target_entity.id) == str(inviter.id):
            raise InviteError("You can't invite yourself")
        if is_blocked(inviter, target_entity):
            raise InviteError("You can't invite this person", 403)
        _assert_not_already_in(target_entity, realm, purpose)

    pending = Invite.objects.filter(
        realm=realm, kind="invite", status="pending", purpose=purpose
    )
    pending = (
        pending.filter(target_entity=target_entity)
        if target_entity is not None
        else pending.filter(target_email=email)
    )
    existing = pending.order_by("-created_at").first()
    if existing:
        return existing, False

    with transaction.atomic():
        invite = Invite.objects.create(
            realm=realm,
            kind="invite",
            status="pending",
            purpose=purpose,
            role=role,
            # Only what was TYPED is stored as the email: a username invite
            # must not hand the inviter the invitee's address in the reply.
            target_email=email,
            target_entity=target_entity,
            created_by=inviter,
        )

        inviter_name = get_entity_display_username(inviter)
        if target_entity is not None:
            notify_invitee(invite, inviter_name)
        if email:
            email_invite(invite, inviter_name, email)

    return invite, True


def invite_link(invite):
    base = getattr(settings, "FRONTEND_URL", "https://chatterloop.app").rstrip("/")
    realm = invite.realm
    return f"{base}{invite_route(invite.invite_token, realm.type, realm.slug)}"


def email_invite(invite, inviter_name, to_email):
    """The emailed link - to the invite's page, which handles signing in."""
    realm = invite.realm
    emailer.send_realm_invite_email(
        to_email=to_email,
        realm_name=realm.name,
        invite_link=invite_link(invite),
        inviter_name=inviter_name,
        subject=f"{inviter_name} invited you on Chatterloop",
        body=(
            invite_sentence(
                inviter_name, realm.name, realm.type, invite.purpose, invite.role
            )
            + "\n\nOpen the invite to accept it:\n"
            + invite_link(invite)
        ),
    )


def notify_invitee(invite, inviter_name=None):
    """
    The in-app half: a notification carrying Accept / Decline, a push, and the
    live event that makes an open client refetch. Best-effort - the invite is
    already saved, and a Mongo or broker hiccup must not undo it.
    """
    target = invite.target_entity
    if target is None:
        return
    realm = invite.realm
    inviter_name = inviter_name or get_entity_display_username(invite.created_by)
    details = invite_sentence(
        inviter_name, realm.name, realm.type, invite.purpose, invite.role
    )
    redirects, actions = invite_notification_ux(
        invite.invite_token, realm.type, realm.slug
    )

    try:
        NotificationService().add_notification(
            referenceID=str(invite.id),
            # False = still open, which is what keeps the buttons up; it flips
            # when the invite is answered (settle_invite_notifications).
            referenceStatus=False,
            # str(): an entity created in this same request - a Google sign-up
            # claiming its invites - still holds a uuid.UUID, which the
            # StringField refuses outright.
            toUserID=str(target.id),
            fromUserID=str(invite.created_by_id),
            content_headline="Invite",
            content_details=details,
            type=INVITE_NOTIFICATION_TYPE,
            isRead=False,
            target_type="invite",
            target_id=invite.invite_token,
            redirects=redirects,
            actions=actions,
        )
        RedisPubSubClient.publish_json(
            f"events_{target.id}",
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
                "dateTime": datetime.now().isoformat(),
            },
        )
    except Exception:
        logger.exception("Failed to write the invite notification")

    try:
        push.send_activity(
            [str(target.id)],
            type=INVITE_NOTIFICATION_TYPE,
            title=realm.name,
            body=details,
            # The apps open the invite page for every realm type.
            route=invite_route(invite.invite_token, platform="android"),
        )
    except Exception:
        logger.exception("Failed to queue the invite push")


def settle_invite_notifications(invite):
    """Drop the buttons from the invite's notification once it is answered."""
    try:
        NotificationService().settle_with_stored_actions(
            str(invite.id), INVITE_NOTIFICATION_TYPE
        )
    except Exception:
        logger.exception("Failed to settle the invite notification")


def claim_pending_invites(account):
    """
    Email invites waiting for this address become in-app invites.

    Called only once the address is PROVEN - a Google sign-up, or the
    verification code. Claiming at registration would let anyone sign up with
    someone else's address and read what they had been invited to.
    """
    if account is None or not account.email or account.entity_id is None:
        return 0
    waiting = Invite.objects.filter(
        kind="invite",
        status="pending",
        target_email__iexact=account.email,
        target_entity__isnull=True,
    ).select_related("realm", "created_by")
    claimed = 0
    for invite in waiting:
        invite.target_entity = account.entity
        invite.save(update_fields=["target_entity"])
        notify_invitee(invite)
        claimed += 1
    return claimed


# --- accepting ------------------------------------------------------------


def _publish(entity_id, event, result):
    try:
        RedisPubSubClient.publish_json(
            f"events_{entity_id}",
            {
                "logType": None,
                "pod": "podless",
                "event": event,
                "message": {"status": True, "auth": True, "result": result},
                "dateTime": datetime.now().isoformat(),
            },
        )
    except Exception:
        logger.exception("Failed to publish %s", event)


def _add_member(entity, realm, added_by, role="member"):
    _, created = Member.objects.get_or_create(
        entity=entity,
        realm=realm,
        defaults={"added_by": added_by, "role": role, "date_joined": now()},
    )
    return created


def post_joined_notice(conversation_id, entity):
    """
    "maya joined" in a conversation, written by the worker once this commits -
    the same line, and the same handle, Node writes when somebody joins on
    their own.
    """
    RabbitMQClient.publish_on_commit(
        Queues.POST_CONVERSATION_NOTICE,
        {
            "conversation_id": conversation_id,
            "actor_entity_id": str(entity.id),
            "text": f"{get_entity_profile_path(entity)} joined",
            "push": True,
        },
    )


def accept_side_effects(invite, entity):
    """
    What accepting an INVITE does, by purpose and realm type. Runs inside the
    caller's transaction; the live events go out on commit.
    """
    realm = invite.realm
    added_by = invite.created_by

    if invite.purpose == "follow":
        from community.views import FollowRealmView

        created, is_pending = follow_entity(entity, realm.entity)
        if created:
            transaction.on_commit(
                lambda: FollowRealmView._notify_new_follower(
                    entity, realm.entity, is_pending
                )
            )
        return

    if invite.purpose == "manage":
        _add_member(entity, realm, added_by, role=invite.role or "moderator")
        return

    joined = _add_member(entity, realm, added_by)

    if realm.type == "group" and joined:
        post_joined_notice(realm.realm_id, entity)

    if realm.type == "server":
        # The server and every PUBLIC channel under it, as Node's server join
        # does (GetServerChannels(serverID, false)) - a private channel takes
        # its members one by one. The line goes in the text channels only; a
        # voice room has no chat.
        channels = Realm.objects.filter(
            parent=realm,
            is_active=True,
            is_private=False,
            type__in=("channel", "voice"),
        )
        for channel in channels:
            if _add_member(entity, channel, added_by) and channel.type == "channel":
                post_joined_notice(channel.realm_id, entity)

    if realm.type in ("server", "group"):
        member_ids = set(
            Member.objects.filter(realm=realm).values_list("entity_id", flat=True)
        )
        member_ids.add(str(entity.id))
        result = {
            "realm_id": realm.realm_id,
            "type": realm.type,
            "action": "joined",
            "entity_ids": [str(entity.id)],
        }
        transaction.on_commit(
            lambda: [
                _publish(member_id, "realm_membership_changed", result)
                for member_id in member_ids
            ]
        )
