"""One locked transition path for existing invitations and review approval."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from community.models import Invite, Member, Realm, Approvals
from community.access import effective_realm_access, lock_realm_mutation
from community.post_review import ReviewConflict
from community.role_policy import require_realm_actor, assert_can_delegate_role
from entity.permissions import Permission
from entity.roles import role_tier, resolve_role
from entity.services.permission_resolver import has_active_permission as has_permission


def invitation_status(actor, realm):
    if realm.type != "community":
        return "pending"
    require_realm_actor(actor, realm)
    if has_permission(actor, Permission.REALM_INVITE_PUBLISH, realm, fresh=True):
        return "pending"
    return "for_approval"


def validate_inviter(invite):
    from community.invites import assert_can_invite
    from user.models import Account
    if not effective_realm_access(invite.realm).allowed:
        raise NotFound("Invite not available")
    if (invite.purpose == 'manage' and invite.realm.type == 'page'
            and str(invite.created_by_id) == str(invite.realm.entity_id)):
        raise PermissionDenied('Revoke and reissue this invitation from an authorized personal account.')
    if Account.objects.filter(entity=invite.created_by, is_active=False).exists():
        raise PermissionDenied("The inviter is no longer eligible.")
    if invite.realm.type == "community":
        require_realm_actor(invite.created_by, invite.realm)
    assert_can_invite(invite.created_by, invite.realm, invite.purpose,
                      role_tier(invite.role) if invite.role_id else None, fresh=True)
    if invite.role_id is not None:
        resolve_role(invite.role, realm=invite.realm)
        target = Member.objects.select_for_update().select_related("role", "entity").filter(
            realm=invite.realm, entity_id=invite.target_entity_id).first()
        assert_can_delegate_role(invite.created_by, invite.realm, target, invite.role)


@transaction.atomic
def notify_invite_review_activity(decision_id):
    from community.admin_notifications import record_admin_activity
    reference = Approvals.objects.values('realm_id', 'target_id').get(pk=decision_id)
    realm = Realm.objects.select_for_update().get(pk=reference['realm_id'])
    invite = Invite.objects.select_for_update().filter(pk=reference['target_id'], realm=realm, status="for_approval").first()
    decision = Approvals.objects.select_for_update(of=('self',)).select_related('actor').get(pk=decision_id)
    decision.realm = realm
    if invite is None:
        decision.delivery_state = {"status": "obsolete"}
        decision.save(update_fields=["delivery_state"])
        return False
    record_admin_activity(decision.realm, dict(referenceID=str(decision.pk), toUserID=str(decision.realm.entity_id),
        fromUserID=str(decision.actor_id), is_anonymous=False, type="community_invite_pending",
        referenceStatus=False,
        content_headline="Invitation awaiting approval", content_details="A member submitted an invitation for approval.",
        target_type="community_approvals", target_id=str(decision.realm_id), target_anchor="invites"), actor_id=decision.actor_id)
    decision.delivery_state = {"status": "complete"}
    decision.save(update_fields=["delivery_state"])
    return True


@transaction.atomic
def deliver_invitation(invite_id):
    from community.invites import notify_invitee, email_invite, get_entity_display_username
    reference = Invite.objects.filter(pk=invite_id).values("realm_id").first()
    if reference is None:
        return False
    realm = Realm.objects.select_for_update().get(pk=reference["realm_id"])
    invite = Invite.objects.select_for_update(of=("self",)).select_related("created_by", "target_entity", "role").get(pk=invite_id)
    invite.realm = realm
    if invite.status != "pending" or not realm.is_active:
        return False
    validate_inviter(invite)
    inviter_name = get_entity_display_username(invite.created_by)
    if invite.target_entity_id:
        notify_invitee(invite, inviter_name)
    if invite.target_email:
        email_invite(invite, inviter_name, invite.target_email)
    return True


@transaction.atomic
def deliver_reviewed_invitation(decision_id):
    from community.invites import notify_invitee, email_invite, get_entity_display_username
    from user_service.services.rabbitmq import RabbitMQClient
    reference = Approvals.objects.filter(pk=decision_id, target_kind="invite").values("realm_id", "target_id").first()
    if reference is None:
        return False
    realm = Realm.objects.select_for_update().get(pk=reference["realm_id"])
    invite = Invite.objects.select_for_update(of=("self",)).select_related("created_by", "role", "target_entity").filter(pk=reference["target_id"]).first()
    decision = Approvals.objects.select_for_update().get(pk=decision_id)
    state = dict(decision.delivery_state)
    if state.get("status") == "complete":
        return True
    from community.admin_notifications import settle_review_notices
    settle_review_notices(realm, 'invite', reference['target_id'])
    if decision.action == 'reject':
        state['status'] = 'complete'
    elif not realm.is_active or invite is None or invite.status != "pending":
        state["status"] = "obsolete"
    else:
        invite.realm = realm
        validate_inviter(invite)
        name = get_entity_display_username(invite.created_by)
        if state.get("notification") != "sent":
            if invite.target_entity_id:
                notify_invitee(invite, name, publisher=RabbitMQClient.publish)
            state["notification"] = "sent"
            decision.delivery_state = state
            decision.save(update_fields=["delivery_state"])
        if state.get("email") != "sent":
            if invite.target_email and not email_invite(invite, name, invite.target_email, publisher=RabbitMQClient.publish):
                return False
            state["email"] = "sent"
        state["status"] = "complete"
    decision.delivery_state = state
    decision.save(update_fields=["delivery_state"])
    return state["status"] == "complete"


def _recipient(invite, actor, account):
    from community.invites import acting_email
    if invite.target_entity_id:
        if str(invite.target_entity_id) != str(actor.pk):
            raise PermissionDenied("This invite belongs to another account.")
    elif not invite.target_email or invite.target_email.lower() != acting_email(actor, account):
        raise PermissionDenied("This invite belongs to another account.")


@transaction.atomic
def cancel_own_join_requests(actor, realm_id, *, server_only=False):
    realm = get_object_or_404(Realm.objects.select_for_update(), pk=realm_id, is_active=True,
        **({"type": "server"} if server_only else {}))
    ids = list(Invite.objects.select_for_update().filter(realm=realm, target_entity=actor,
        kind="request", status="pending").order_by("pk").values_list("pk", flat=True))
    for invite_id in ids:
        transition_invite(actor=actor, invite_id=invite_id, action="revoked")
    return ids


@transaction.atomic
def transition_invite(*, actor, account=None, invite_id=None, invite_token=None, action,
                      expected_status=None, realm_id=None):
    from community.invites import accept_side_effects, settle_invite_notifications, is_blocked
    from community.views import InviteView
    if not isinstance(action, str):
        raise ValidationError({"action": "Choose a supported invitation action."})
    action = action.strip().lower()
    reference = get_object_or_404(Invite, **({"pk": invite_id} if invite_id else {"invite_token": invite_token}))
    if realm_id is not None and str(reference.realm_id) != str(realm_id):
        raise NotFound("Invite not available")
    accepting = action in ('accepted', 'approve')
    realm = lock_realm_mutation(reference.realm, actor,
        reference.target_entity if accepting else None,
        reference.created_by if accepting and reference.kind == 'invite' else None)
    invite = Invite.objects.select_for_update(of=("self",)).select_related("role", "created_by", "target_entity").get(pk=reference.pk)
    if (action == 'revoked' and invite.kind == 'invite' and invite.purpose == 'manage'
            and realm.type == 'page' and str(actor.pk) == str(realm.entity_id)):
        actor = getattr(account, 'entity', None)
        if actor is None:
            raise PermissionDenied('Your personal authority is required to revoke this invitation.')
    invite.realm = realm
    review = action in ("approve", "reject") and invite.kind == "invite"
    if not review and invite.kind == "invite" and action in ("accepted", "declined") and invite.status in ("for_approval", "rejected"):
        raise NotFound("Invite not available")
    if expected_status is not None and invite.status != expected_status:
        raise ReviewConflict("This invite has already changed.")
    if review:
        if (realm.type != "community" or expected_status not in ("for_approval", "rejected")
                or invite.status not in ("for_approval", "rejected")
                or (action == "reject" and invite.status != "for_approval")):
            raise ReviewConflict()
        require_realm_actor(actor, realm)
        if not has_permission(actor, Permission.REALM_INVITE_REVIEW, realm, fresh=True):
            raise PermissionDenied("You cannot review this invitation.")
        if str(invite.created_by_id) == str(actor.pk) and not has_permission(actor, Permission.REALM_INVITE_PUBLISH, realm, fresh=True):
            raise PermissionDenied("You cannot approve your own restricted invitation.")
        if action == "approve":
            validate_inviter(invite)
        previous = {"status": invite.status}
        invite.status = "pending" if action == "approve" else "rejected"
        invite.save(update_fields=["status"])
        decision = Approvals.objects.create(realm=realm, actor=actor, target_kind="invite", target_id=str(invite.pk),
            action=action, previous_state=previous, new_state={"status": invite.status},
            delivery_state={"status": "pending"})
        transaction.on_commit(lambda: deliver_reviewed_invitation(str(decision.pk)), robust=True)
        return invite
    if invite.status not in ("pending", "for_approval") or (invite.status == "for_approval" and action != "revoked"):
        raise ReviewConflict("This invite has already been answered.")
    status = {"approve": "accepted", "reject": "declined"}.get(action, action)
    if status not in ("accepted", "declined", "revoked"):
        raise ValidationError({"action": "Choose a supported invitation action."})
    if invite.kind == "invite" and status != "revoked":
        _recipient(invite, actor, account)
    elif invite.kind == "request":
        permission = Permission.REALM_REQUEST_APPROVE if status == "accepted" else Permission.REALM_REQUEST_DECLINE
        own_withdrawal = status in ("declined", "revoked") and str(invite.target_entity_id) == str(actor.pk)
        if not own_withdrawal:
            require_realm_actor(actor, realm)
            if not has_permission(actor, permission, realm, fresh=True):
                raise PermissionDenied("You cannot review this join request.")
    if status == "revoked" and invite.kind == "invite" and str(invite.created_by_id) != str(actor.pk):
        if not has_permission(actor, Permission.REALM_INVITE_REVOKE, realm, fresh=True):
            raise PermissionDenied("You cannot revoke this invitation.")
    target = invite.target_entity or actor
    if status == "accepted":
        from user.models import Account
        if (target.type == "user" and not Account.objects.filter(entity=target, is_active=True).exists()) or (
            target.type == "realm" and not Realm.objects.filter(entity=target, type="page", is_active=True).exists()):
            raise PermissionDenied("The recipient is no longer eligible.")
        if Realm.objects.filter(entity=target, type="community").exists() or is_blocked(invite.created_by, target):
            raise PermissionDenied("This entity cannot join the realm.")
        if invite.kind == "invite":
            validate_inviter(invite)
        elif invite.target_entity is None:
            raise NotFound("Request not available")
        if invite.kind == "request" and realm.type in ("community", "conference"):
            InviteView()._add_member_if_missing(invite, actor)
        else:
            accept_side_effects(invite, target)
        invite.accepted_by_entity = actor
    before = {"status": invite.status}
    invite.status, invite.resolved_at = status, now()
    invite.save(update_fields=["status", "resolved_at", "accepted_by_entity"])
    if realm.type == "community":
        Approvals.objects.create(realm=realm, actor=actor, target_kind="join" if invite.kind == "request" else "invite",
            target_id=str(invite.pk), action="approve" if status == "accepted" else "reject" if status == "declined" else "revoke",
            previous_state=before, new_state={"status": status})
    view = InviteView()
    if invite.kind == "invite":
        transaction.on_commit(lambda: settle_invite_notifications(invite), robust=True)
        if realm.type == 'community' and before['status'] == 'for_approval':
            from community.admin_notifications import settle_review_notices
            transaction.on_commit(lambda: settle_review_notices(realm, 'invite', invite.pk), robust=True)
    if status == "accepted":
        transaction.on_commit(lambda: view._notify_members_changed(realm), robust=True)
    if invite.kind == "request":
        if realm.type == "community":
            from community.admin_notifications import settle_join_requests
            transaction.on_commit(lambda: settle_join_requests([str(invite.pk)]), robust=True)
        transaction.on_commit(lambda: view._notify_requests_changed(realm), robust=True)
        transaction.on_commit(lambda: view._notify_access_changed(invite.target_entity_id, realm), robust=True)
    return invite
