"""
@mentions inside comments.

Same model as mentions in MESSAGES: the mention IS the text. "@ana" sits in
Comment.text like any other characters, nothing is stored alongside it, and the
client highlights it at render time (see the messenger's ContentHandler, which
wraps a matched handle in .cl-message-mention). No mention table, no ids on the
wire - a comment's text is the whole truth.

What the server still has to do is the one thing the messenger gets for free:
a conversation's participants are already there to be notified, while someone
mentioned in a comment may have nothing to do with the post. So handles are
parsed out on write purely to send that notification, and nothing about the
parse is persisted.
"""

import re
from datetime import datetime

from django.db.models import Q

from entity.models import Entity
from entity.utils import get_entity_display_username
from user.services.mongohelpers import NotificationService
from user.utils.blocking import get_blocked_account_ids
from user_service.services.redis import RedisPubSubClient

# A comment is short form; past this the input is a spam vector rather than a
# conversation, and every mention costs a notification write + an SSE publish.
MAX_MENTIONS_PER_COMMENT = 20

# Deliberately character-for-character the Node side's extractMentionUsernames
# (server/reusables/hooks/transformers.js), because both services parse the
# SAME convention and a comment mention that the messenger wouldn't count as
# one is a bug in whichever of the two drifted.
#
# The leading (?:^|\s) is what stops "you@example.com" from mentioning
# @example. Over-long input matches nothing rather than being truncated to a
# different handle, which is the safe direction.
#
# Note the class includes "." and is greedy, so "thanks @ana." captures
# "ana." - the trailing lookahead is already satisfied by end-of-string with
# the dot consumed. extract_mention_handles() emits the dot-stripped form as
# an extra candidate to cover it; the pattern itself is left alone so the two
# services keep extracting identically.
MENTION_PATTERN = re.compile(r"(?:^|\s)@([A-Za-z0-9._-]{1,30})(?=$|\s|[.,!?;:])")

# "Mentionable" is the same bar entity_side_is_visible() applies to
# connections and follows - an active + verified account, an active realm, or
# an active bot - but written for a query rooted ON Entity rather than on a row
# pointing at one, so that helper's `{field}__` prefix form doesn't fit.
#
# Bots are here because a bot that cannot be mentioned cannot be addressed at
# all: the whole comment-mention path exists to notify whoever was named, and
# an entity absent from this predicate is silently not named no matter what the
# text says. Not required to be verified, for the same reason a realm is not -
# a bot has no verification concept to gate on.
MENTIONABLE = (
    Q(users__is_active=True, users__is_verified=True)
    | Q(realms__is_active=True)
    | Q(bots__is_active=True)
)


def extract_mention_handles(text):
    """
    Candidate handles written as "@handle" in `text`, deduplicated, order
    preserved.

    Lowercased because the lookup is case-insensitive (the same
    toLowerCase-on-both-sides the /sendMessage handler does), so "@Ana" and
    "@ana" must not spend two of the MAX_MENTIONS budget on one person.

    A handle ending in "." also yields its stripped form, so "thanks @ana."
    offers both "ana." and "ana". Only one of them can be a real handle, so
    resolution stays unambiguous - and a page whose slug genuinely ends in a
    dot still works.
    """
    if not text:
        return []

    seen = []
    for raw_handle in MENTION_PATTERN.findall(text):
        handle = raw_handle.lower()
        for candidate in (handle, handle.rstrip(".")):
            if candidate and candidate not in seen:
                seen.append(candidate)
        if len(seen) >= MAX_MENTIONS_PER_COMMENT:
            break
    return seen


def resolve_mentioned_entities(text, author_entity):
    """
    Entities actually addressed by the "@handle" tokens in `text`.

    Resolves against ALL THREE handle namespaces - Account.username, Realm.slug
    and Bot.handle - so a page or a bot can be mentioned exactly like a person,
    and applies the same visibility and blocking rules the rest of the graph
    uses. A handle that matches nothing is just text, which is the whole point
    of this design: typing "@" costs nothing and breaks nothing.

    The three namespaces are NOT mutually exclusive today - bot handles are
    unique only among bots, so a bot and a user can currently share one, which
    `bot/models.py` names as a real gap it cannot close alone. Where that
    happens both entities resolve and both are notified, which is the safe
    direction: over-notifying is a nuisance, silently addressing the wrong
    entity is a bug.

    The author's own handle is dropped here rather than at notify time -
    nobody needs telling they wrote their own comment.
    """
    if author_entity is None:
        return []

    handles = extract_mention_handles(text)
    if not handles:
        return []

    blocked_ids = {str(bid) for bid in get_blocked_account_ids(author_entity)}

    # iexact per handle rather than one __in against the lowercased list:
    # usernames are generated lowercase but are editable afterwards, and a
    # slug is free text, so "@Ana" has to find a stored "Ana". At most
    # MAX_MENTIONS_PER_COMMENT handles reach here, and this is a write path.
    handle_match = Q()
    for handle in handles:
        handle_match |= (
            Q(users__username__iexact=handle)
            | Q(realms__slug__iexact=handle)
            | Q(bots__handle__iexact=handle)
        )

    resolved = (
        Entity.objects.filter(MENTIONABLE, handle_match)
        .exclude(id=author_entity.id)
        .select_related("users", "realms", "bots")
        .distinct()
    )

    return [
        entity for entity in resolved if str(entity.id) not in blocked_ids
    ][:MAX_MENTIONS_PER_COMMENT]


def notify_comment_mentions(comment, author_entity, entities, already_notified_ids=()):
    """
    Notification + SSE ping per mentioned entity.

    `already_notified_ids` carries the entity ids the surrounding view has
    ALREADY pinged for this same comment (the post author, the author of the
    comment being replied to). Without it, being mentioned in a reply to your
    own comment arrives twice for one event.
    """
    skip = {str(entity_id) for entity_id in already_notified_ids if entity_id}
    skip.add(str(author_entity.id))

    mention_text = (
        f"{get_entity_display_username(author_entity)} mentioned you in a comment."
    )
    service = NotificationService()

    for entity in entities:
        if str(entity.id) in skip:
            continue

        service.add_notification(
            referenceID=comment.comment_id,
            # The post the comment lives on, anchored at the comment doing the
            # mentioning.
            target_type="post",
            target_id=comment.post_id,
            target_anchor=comment.comment_id,
            referenceStatus=True,
            toUserID=entity.id,
            fromUserID=author_entity.id,
            content_headline="Comment Mention",
            content_details=mention_text,
            type="comment_mention",
            isRead=False,
        )

        RedisPubSubClient.publish_json(
            f"events_{entity.id}",
            {
                "logType": None,
                "pod": "podless",
                "event": "notifications",
                "message": {
                    "status": True,
                    "auth": True,
                    "message": mention_text,
                    "result": "",
                },
                "dateTime": datetime.now().isoformat(),
            },
        )

        skip.add(str(entity.id))
