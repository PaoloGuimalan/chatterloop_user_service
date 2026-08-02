"""
Who is allowed to see which post.

ONE definition of post audience, expressed as a Q() so it composes into any
existing Post queryset instead of being re-derived per view. Every read path
that returns posts - home feed, profile feed, preview-by-id, search - applies
`visible_posts_filter(viewer_entity)`; a path that forgets to is a leak, so
prefer adding the filter over hand-rolling a privacy check.

Audience levels (Post.PRIVACY_STATUS_CHOICES):

  public       everyone, including guests
  connections  the author + their accepted connections. This is what a
               private profile posts with by default.
  private      the author only
  custom       the author + the explicit allow-list in PostPrivacy

`viewer_entity` is None for a guest, which collapses to "public only".

Note this is about the POST's audience, not the author's profile privacy.
The two are related but separate: flipping a profile to private rewrites its
existing public posts to connections-only (see
apply_profile_privacy_to_posts), and after that the post rows carry the
restriction on their own - which is why flipping back to public deliberately
does not undo it.
"""

from django.db.models import Exists, OuterRef, Q

from entity.models import Connection
from entity.utils import entity_side_is_visible


def _accepted_connection_subquery(viewer_entity):
    """
    EXISTS() over an accepted connection between the post's author and the
    viewer, in either direction.

    A connection is two mirrored rows sharing a connection_id, both flipped
    to status=True on accept, so matching a single row with status=True is
    enough to prove the handshake - no need to count both.

    Correlated on Post.entity_id via OuterRef, so this is only valid nested
    inside a Post queryset. The single-object check uses
    _has_accepted_connection() instead, which takes a concrete author id.
    """
    return Connection.objects.filter(
        Q(action_by=OuterRef("entity_id"), involved_entity=viewer_entity)
        | Q(action_by=viewer_entity, involved_entity=OuterRef("entity_id")),
        entity_side_is_visible("action_by"),
        entity_side_is_visible("involved_entity"),
        status=True,
    )


def _has_accepted_connection(viewer_entity, author_id):
    """Uncorrelated twin of the subquery above, for a known author id."""
    return Connection.objects.filter(
        Q(action_by_id=author_id, involved_entity=viewer_entity)
        | Q(action_by=viewer_entity, involved_entity_id=author_id),
        entity_side_is_visible("action_by"),
        entity_side_is_visible("involved_entity"),
        status=True,
    ).exists()


def visible_posts_filter(viewer_entity):
    """
    Q() selecting the posts `viewer_entity` may read.

    Intended to be combined with the caller's own filters, e.g.
        Post.objects.filter(visible_posts_filter(entity), deleted_at=None)

    Callers that also annotate must apply this as a .filter() argument rather
    than folding it into an annotation - it can join through PostPrivacy, so
    pair it with .distinct() where the surrounding query is not already
    de-duplicated by an id__in.
    """
    if viewer_entity is None:
        return Q(privacy_status="public")

    return (
        Q(privacy_status="public")
        # Your own posts are always yours to read, at every level.
        | Q(entity=viewer_entity)
        | Q(
            Q(privacy_status="connections")
            & Q(Exists(_accepted_connection_subquery(viewer_entity)))
        )
        | Q(
            Q(privacy_status="custom")
            & Q(privacy_users__allowed_entity=viewer_entity)
        )
    )


def can_view_post(post, viewer_entity):
    """
    Single-object form of the same rule, for paths that already hold a Post
    and only need a yes/no (preview-by-id, comment reads).

    Kept beside the queryset version deliberately: the two must agree, so
    they should be read - and changed - together.
    """
    if post is None:
        return False

    privacy = post.privacy_status or "public"

    if privacy == "public":
        return True

    if viewer_entity is None:
        return False

    if str(post.entity_id) == str(viewer_entity.id):
        return True

    if privacy == "connections":
        return _has_accepted_connection(viewer_entity, post.entity_id)

    if privacy == "custom":
        return post.privacy_users.filter(allowed_entity=viewer_entity).exists()

    # "private" - author only, and we already know the viewer is not them.
    return False


def apply_profile_privacy_to_posts(entity):
    """
    Narrow an author's existing PUBLIC posts to connections-only, used when a
    profile is switched to private.

    Deliberately one-way and deliberately partial:

    * Only "public" rows are touched. A post the author had already narrowed
      to private/custom must not be widened to connections by this.
    * Switching back to public does NOT reverse it. Un-privating a profile
      would otherwise re-publish, in bulk and silently, posts the author
      wrote while expecting a closed audience - the profile setting decides
      the DEFAULT audience for what comes next, not retroactively for what
      is already written. Individual posts can still be re-shared publicly
      one at a time from the post editor.

    Returns the number of posts narrowed.
    """
    from newsfeed.models import Post

    if entity is None:
        return 0

    return Post.objects.filter(entity=entity, privacy_status="public").update(
        privacy_status="connections"
    )


def default_privacy_status_for(entity):
    """
    The audience a NEW post by `entity` should default to: connections-only
    for a private profile, public otherwise.

    Server-side default rather than a client concern - the Node createpost
    route calls the equivalent of this so a client that omits (or lies about)
    the privacy field cannot publish a private user's post to everyone.
    """
    from entity.services.follows import entity_is_private

    return "connections" if entity_is_private(entity) else "public"
