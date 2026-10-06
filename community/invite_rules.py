"""
The rules of a realm invite that need no database - what each realm type can
be invited into, which roles a page invite may carry, how a target typed into
the invite box is read, and the notification an invitee gets.

Kept free of Django model imports so it can be unit tested without settings
(community/tests/test_invite_rules.py runs under plain `python -m unittest`).
"""

# What an invite asks the invitee to do, per realm type:
#   join    - become a member (a group, a server and its channels, a
#             conference room)
#   manage  - join a PAGE's team, as admin or moderator
#   follow  - follow a PAGE
PURPOSES_BY_REALM_TYPE = {
    "group": ("join",),
    "server": ("join",),
    "conference": ("join",),
    "page": ("manage", "follow"),
}

# The roles a "manage" invite may offer. Owner is never handed out by invite -
# ownership moves only through an explicit transfer.
MANAGE_ROLES = ("admin", "moderator")


class InviteError(Exception):
    """A request the invite rules refuse - carries the reply's status code."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def resolve_purpose(realm_type, purpose=None, role=None):
    """
    The (purpose, role) an invite into `realm_type` will carry, or InviteError.

    A realm type with ONE purpose takes it when none is given, so the existing
    conference and group callers need not send one. A page has two and must
    say which. A role is only meaningful for "manage", and is required there.
    """
    allowed = PURPOSES_BY_REALM_TYPE.get(realm_type)
    if not allowed:
        raise InviteError("This kind of realm can't be invited into")

    purpose = (purpose or "").strip().lower() or None
    if purpose is None:
        if len(allowed) > 1:
            raise InviteError("Say whether this is an invite to follow or to manage")
        purpose = allowed[0]
    if purpose not in allowed:
        raise InviteError(f"A {realm_type} can't be invited into to {purpose}")

    if purpose == "manage":
        role = (role or "").strip().lower()
        if role not in MANAGE_ROLES:
            raise InviteError("Pick a role: admin or moderator")
        return purpose, role
    return purpose, None


def parse_target(raw):
    """
    What was typed into the invite box: ("email", address), ("username",
    handle) or InviteError.

    An "@" anywhere but the front makes it an email; a leading "@" is a
    username, as people type them in a mention.
    """
    value = (raw or "").strip()
    if not value:
        raise InviteError("Enter an email address or a username")
    if "@" in value[1:]:
        local, _, domain = value.partition("@")
        if not local or "." not in domain or " " in value:
            raise InviteError("That email address doesn't look right")
        return "email", value.lower()
    handle = value[1:] if value.startswith("@") else value
    if not handle or " " in handle:
        raise InviteError("That username doesn't look right")
    return "username", handle


def invite_sentence(inviter_name, realm_name, realm_type, purpose, role=None):
    """The sentence an invitee reads - notification, push and email alike."""
    if purpose == "manage":
        article = "an" if role == "admin" else "a"
        return f"{inviter_name} invited you to help run {realm_name} as {article} {role}."
    if purpose == "follow":
        return f"{inviter_name} invited you to follow {realm_name}."
    noun = {
        "group": "the group",
        "server": "the server",
        "conference": "the conference",
    }.get(realm_type, "")
    return f"{inviter_name} invited you to join {noun} {realm_name}.".replace("  ", " ")


INVITES_PATH = "/api/realm/invites"
PLATFORMS = ("web", "android", "ios")


def invite_route(token, realm_type=None, realm_slug=None, platform="web"):
    """
    Where an invite opens: its own page, the same path on all three clients -
    except a CONFERENCE on the web, which opens the conference itself. Its
    lobby reads ?invite_token= and lets the invitee in, guests included
    (webapp ConferenceRoom.tsx), and every conference invite emailed before
    this page existed links there. The apps have no conference screens, so
    they get the invite page either way.
    """
    if platform == "web" and realm_type == "conference" and realm_slug:
        return f"/conference/{realm_slug}?invite_token={token}"
    return f"/invite/{token}"


def invite_notification_ux(token, realm_type=None, realm_slug=None):
    """
    The stored `redirects` and `actions` of an invite notification.

    STORED rather than derived (server notificationactions.js derives buttons
    only for contact and follow requests): each pair of buttons addresses ONE
    invite by its token, so they live on the document. Invites are never
    grouped (no spec in server notificationgroups.js lists them) - each is
    about a different realm.

    Tapping the row opens the invite page, which says what it is and offers
    the same two answers.
    """
    redirects = [
        {
            "platform": platform,
            "type": "invite",
            "route": invite_route(token, realm_type, realm_slug, platform),
        }
        for platform in PLATFORMS
    ]
    actions = []
    for platform in PLATFORMS:
        for order, (action_id, name, style, answer) in enumerate(
            (
                ("accept", "Accept", "primary", "accepted"),
                ("decline", "Decline", "danger", "declined"),
            )
        ):
            actions.append(
                {
                    "platform": platform,
                    "id": action_id,
                    "name": name,
                    "type": "api-request",
                    "style": style,
                    "order": order,
                    "after": "refresh",
                    "route": None,
                    "url": INVITES_PATH,
                    "service": "user",
                    "method": "PATCH",
                    "payload": {"invite_token": token, "status": answer},
                    "headers": None,
                }
            )
    return redirects, actions
