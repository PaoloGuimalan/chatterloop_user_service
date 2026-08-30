"""
The moderation detail surface - "why was my content removed?"

THE ONE PLACE SOFT-DELETED CONTENT IS STILL READABLE
----------------------------------------------------
Every other read path filters `deleted_at IS NULL`, which is exactly right:
removed content should not appear in a feed, a profile or a search. But a person
told their post was removed has to be able to SEE what was removed and why, or
the notification is an accusation with no evidence attached. This endpoint is
that exception, and it is deliberately the only one.

Because it is an exception, the access rule is the whole design:

* the OWNER of the content, who is the person being told; and
* platform STAFF, who need to review what the automation did.

Nobody else - not the reporters, not the people who could see the post before it
was removed. Widening this turns a moderation record into a way to read deleted
content.

WHERE THE REASON COMES FROM
---------------------------
The moderation document in Mongo, written by moderation_service. Django reads it
rather than duplicating the verdict into Postgres: the classifier's output
belongs to the service that produced it, and copying it would leave two records
of the same judgement that could disagree after a re-analysis.
"""

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Exists, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from newsfeed.models import Comment, Post, PostSave, Reaction
from newsfeed.serializers import PostSerializer
from user.ext_models.mongomodels import ModerationRecord

logger = logging.getLogger(__name__)


def _is_staff(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and (user.is_staff or user.is_superuser))


class ModerationDetailView(APIView):
    """
    GET /api/entity/moderation/<moderation_id>/

    The content that was acted on, the verdict behind it, and enough of the
    reasoning to argue with. Renders soft-deleted content - see the module
    docstring for why that is safe here and nowhere else.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, moderation_id):
        try:
            record = ModerationRecord.objects(moderationID=moderation_id).first()
            if record is None:
                return Response(
                    {"status": False, "message": "Moderation record not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            content = self._resolve_content(record, request)
            if content is None:
                # The record survives its content: a hard delete elsewhere, or a
                # source type with nothing to show. Not an error - there is
                # simply nothing left to render.
                return Response(
                    {"status": False, "message": "The content is no longer available."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            entity = getattr(request, "entity", None)
            is_owner = entity is not None and str(content["owner_entity_id"]) == str(
                entity.id
            )

            if not (is_owner or _is_staff(request)):
                # 404 rather than 403. A 403 confirms the record exists and that
                # somebody's content was removed, which is not this caller's
                # business to learn.
                return Response(
                    {"status": False, "message": "Moderation record not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(
                {
                    "status": True,
                    "data": {
                        "moderation_id": record.moderationID,
                        "content": content["payload"],
                        "moderation": self._verdict(record),
                        "viewer_is_owner": is_owner,
                    },
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("ModerationDetailView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _resolve_content(self, record, request):
        """The post or comment behind a moderation record, deleted or not.

        An ATTACHMENT's record targets the attachment, not the post - so the
        parent is resolved out of foreignID, the same way moderation_service's
        interest sink does it: by elimination rather than by position, so this
        does not depend on the order the ids were appended in.
        """
        source = record.sourceType or ""
        target_id = record.targetID
        foreign = list(record.foreignID or [])

        if source.startswith("comment"):
            comment_id = target_id
            if source != "comment":
                comment_id = next((f for f in foreign if f != target_id), None)
            comment = (
                Comment.objects.select_related("entity", "post")
                .filter(comment_id=comment_id)
                .first()
            )
            if comment is None:
                return None
            return {
                "owner_entity_id": comment.entity_id,
                "payload": {
                    "type": "comment",
                    "id": comment.comment_id,
                    "post_id": comment.post_id,
                    "text": comment.text,
                    "attachment": comment.attachment,
                    "created_at": comment.created_at,
                    "deleted_at": comment.deleted_at,
                    "is_removed": comment.deleted_at is not None,
                },
            }

        post_id = target_id
        if source != "post":
            post_id = next((f for f in foreign if f != target_id), None)

        # Serialized with the SAME annotations the feed uses, because the
        # client renders this through the ordinary PostItem rather than a
        # bespoke read-only view - one renderer, so a removed post looks like
        # the post it was. PostSerializer declares `entity_reaction` and
        # `is_saved` as real fields, so they have to be annotated here or the
        # serializer raises rather than omitting them.
        viewer = getattr(request, "entity", None)
        post = (
            Post.objects.select_related("entity", "score")
            .prefetch_related(
                "tagging", "privacy_users", "references", "map_info", "preview"
            )
            .annotate(
                is_saved=Exists(
                    PostSave.objects.filter(post=OuterRef("pk"), entity=viewer)
                ),
                entity_reaction=Coalesce(
                    Subquery(
                        Reaction.objects.filter(
                            post=OuterRef("pk"), entity=viewer
                        ).values("emoji_id")[:1]
                    ),
                    Value(None),
                ),
            )
            .filter(post_id=post_id)
            .first()
        )
        if post is None:
            return None

        payload = PostSerializer(post).data
        # Flags the client keys off, alongside the post's own fields.
        payload["type"] = "post"
        # WHICH attachment was judged, when the record is about one.
        #
        # Media moderation targets a single reference, not the post - a post
        # with four photos and one violating frame produces a record whose
        # targetID is that photo. Without this the review shows four images and
        # leaves the reader guessing which one it is about.
        payload["flagged_reference_id"] = (
            target_id if source.endswith("_attachment") else None
        )
        payload["is_removed"] = post.deleted_at is not None

        return {"owner_entity_id": post.entity_id, "payload": payload}

    def _verdict(self, record):
        """What the classifier decided, in the shape a person can read.

        Category scores are included because "we think this is nudity, 0.68" is
        arguable in a way "this broke the rules" is not - and arguing with it is
        the point of showing it.
        """
        moderation = record.moderation or {}
        categories = moderation.get("categories") or []

        return {
            "verdict": moderation.get("verdict"),
            "top_score": moderation.get("topScore"),
            "categories": [
                {"code": entry.get("code"), "score": entry.get("score")}
                for entry in categories
            ],
            # A category nobody checked is not a category that came back clean.
            # Surfaced so the record cannot be read as a clean bill of health on
            # everything it does not mention.
            "unevaluated": moderation.get("unevaluated") or [],
            "removed": moderation.get("removed", False),
            "enforced_at": moderation.get("enforcedAt"),
            "report_id": moderation.get("reportID"),
            # What the model actually saw - the caption or transcript it judged.
            # For an image or a video this is the only human-readable account of
            # why it scored the way it did.
            "reviewed_text": record.text or record.transcription or record.caption,
        }
