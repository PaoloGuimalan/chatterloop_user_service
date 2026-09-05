from django.db.models.signals import post_save, post_delete
from django.utils.timezone import now
from django.dispatch import receiver
from .models import (
    Post,
    Comment,
    Reaction,
)
from user.models import UserEngagementLog
from django.core.cache import cache
from user_service.services.rabbitmq import RabbitMQClient, Queues


# PreviewCount rows are NOT pre-seeded any more - neither per new emoji
# (which wrote one row per existing post, so adding an emoji cost a write per
# post and got worse as the site grew) nor per new post (which made the table
# posts x emojis, almost entirely zeros).
#
# A zero row and a missing row are indistinguishable to every reader: the
# clients render `preview.filter(count > 0)`, totals sum to the same number,
# and the emoji picker reads the Emoji table rather than this one. So the
# reaction endpoints create the row on first use via get_or_create instead,
# and the (post, emoji) unique constraint keeps that safe under concurrency.


@receiver(post_save, sender=Post)
def create_post_score_for_new_post(sender, instance, created, **kwargs):
    """
    Seed the post's score row.

    The scoring itself now lives in the worker. Deferred to COMMIT because the
    handler reads newsfeed_postreference to weight the post by its media, and
    those rows are written in the same transaction as the Post - publishing
    inline races them and scores the post as if it had no attachments.
    """
    if created:
        RabbitMQClient.publish_on_commit(
            Queues.CREATE_POST_SCORE_FOR_NEW_POST,
            {"post_id": instance.post_id, "date_posted": instance.date_posted},
        )


@receiver(post_save, sender=Comment)
def log_comment_action(sender, instance, created, **kwargs):
    if created:
        # EngagementLog.objects.create(
        #     post=instance.post,
        #     user=instance.user,
        #     action="commented",  # → reference_id = comment.comment_id
        #     reference_id=instance.comment_id,
        # )

        log = UserEngagementLog(
            user_id=str(instance.entity.id),
            activity_time=now(),
            time_spent=float(0),
            activity_type="comment",
            target_type="post",
            target_id=str(instance.comment_id),
        )
        log.save()

        # bump interaction_score

        if instance.entity != instance.post.entity:
            RabbitMQClient.publish_on_commit(
                Queues.INTERACTION_SCORE_BUMP,
                {
                    "actor_id": instance.entity.id,
                    "receiver_id": instance.post.entity.id,
                    "action": "COMMENT",
                    "is_decrease": False,
                },
            )

        if instance.post.entity:
            RabbitMQClient.publish_on_commit(
                Queues.FOLLOWER_INTERACTION_SCORE_BUMP,
                {
                    "actor_id": instance.entity.id,
                    "receiver_id": instance.post.entity.id,
                    "action": "COMMENT",
                    "is_decrease": False,
                },
            )

        # fan-out to timelines

        lock_key = f"chatterloop:bump_lock:{str(instance.post.post_id)}:{str(instance.entity.id)}:comment"

        # The lock STAYS here. It is what makes this one fan-out per
        # (post, commenter) per 30 minutes rather than one per comment, and the
        # worker knows nothing about it - publishing unguarded would re-fan the
        # post to 500 buckets on every reply.
        if cache.add(lock_key, "active", timeout=1800):
            # Social bump: someone commented, so push the post into the feeds
            # of the people who follow THEM. Two different entities here, which
            # the payload keeps apart: the buckets belong to the COMMENTER's
            # followers (current_entity_id), while the row records the POST's
            # author. Resolving those followers is the worker's job now, so
            # get_follower_ids is gone from this path.
            #
            # "comment", not the default "fanout": this is the one path that
            # puts a post in front of someone who may not follow its author at
            # all, so it is the one the feed has to explain - it renders as
            # "@handle commented on this post" above the card. The worker
            # stores the commenter as triggered_by (it is current_entity_id),
            # which is who that caption names.
            RabbitMQClient.publish_on_commit(
                Queues.BULK_FANOUT_TO_CACHE,
                {
                    "current_entity_id": instance.entity_id,
                    "post_data": {
                        "id": instance.post.post_id,
                        "author_id": instance.post.entity_id,
                    },
                    "type": "comment",
                },
            )


@receiver(post_delete, sender=Comment)
def remove_comment_log(sender, instance, **kwargs):
    """
    Retract the engagement log a comment wrote when it was created.

    NOTE this receiver is effectively dormant: CommentsView.delete SOFT-deletes
    (sets deleted_at), and so does account deletion, so post_delete never fires
    for a Comment today. Wired anyway so a future hard delete cleans up after
    itself, but the existing comment logs are NOT being retracted - see the
    reaction receiver below, which is the one that actually runs.

    Deferred to COMMIT: the log should only go if the row really went.
    """
    RabbitMQClient.publish_on_commit(
        Queues.REMOVE_ENGAGEMENT_LOG,
        {
            "entity_id": instance.entity_id,
            "activity_type": "comment",
            "target_type": "post",
            "target_id": instance.comment_id,
        },
    )


@receiver(post_save, sender=Reaction)
def log_reaction_action(sender, instance, created, **kwargs):
    if created:
        # EngagementLog.objects.create(
        #     post=instance.post,
        #     user=instance.user,
        #     action="reacted",  # → reference_id = reaction.reaction_id
        #     reference_id=instance.reaction_id,
        # )

        log = UserEngagementLog(
            user_id=str(instance.entity.id),
            activity_time=now(),
            time_spent=float(0),
            activity_type="react",
            target_type="post",
            target_id=str(instance.reaction_id),
        )
        log.save()


@receiver(post_delete, sender=Reaction)
def remove_reaction_log(sender, instance, **kwargs):
    """
    Retract the engagement log a reaction wrote when it was created.

    Unlike the comment receiver above this one is live: un-reacting really
    deletes the row (PostReactionsView.delete), as does account deletion, so it
    fires on every removed reaction.
    """
    RabbitMQClient.publish_on_commit(
        Queues.REMOVE_ENGAGEMENT_LOG,
        {
            "entity_id": instance.entity_id,
            "activity_type": "react",
            "target_type": "post",
            "target_id": instance.reaction_id,
        },
    )


# Add Share Engagement Log to Server Express JS API
