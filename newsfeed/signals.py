from django.db.models.signals import post_save, post_delete
from django.utils.timezone import now
from django.dispatch import receiver
from .models import (
    Post,
    PostReference,
    PostScore,
    Comment,
    Reaction,
)
from user.models import UserEngagementLog
from entity.services.follows import get_follower_ids
from .helpers.query_functions import (
    bulk_fanout_to_cache,
    interaction_score_bump,
    follower_interaction_score_bump,
)
from django.core.cache import cache


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
    if created:
        references = PostReference.objects.filter(post=instance)

        content_t_m = 1.0

        if len(references) > 0:
            for reference in references:
                if reference.reference_media_type == "image":
                    content_t_m += 1.2
                elif reference.reference_media_type == "video":
                    content_t_m += 1.5
                else:
                    content_t_m += 1.0
        else:
            content_t_m += 0.0

        final_content_score = content_t_m / (len(references) + 1)

        age_hours = (now() - instance.date_posted).total_seconds() / 3600
        affinity_score = 1.0
        content_type_weight = final_content_score
        recent_update_boost = 1.0
        comments_count = 0
        likes_count = 0
        shares_count = 0

        weighted_engagement = comments_count * 3 + likes_count * 1 + shares_count * 5
        decay_factor = (age_hours + 1) ** 1.2
        ranking_score = (
            (weighted_engagement / decay_factor)
            * affinity_score
            * content_type_weight
            * recent_update_boost
        )

        PostScore.objects.update_or_create(
            post=instance,
            defaults={
                "affinity_score": affinity_score,
                "content_type_weight": content_type_weight,
                "recent_update_boost": recent_update_boost,
                "likes_count": likes_count,
                "comments_count": comments_count,
                "shares_count": shares_count,
                "ranking_score": ranking_score,
            },
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
            interaction_score_bump(
                instance.entity.id, instance.post.entity.id, "COMMENT", False
            )

        if instance.post.entity:
            follower_interaction_score_bump(
                instance.entity.id,
                instance.post.entity.id,
                "COMMENT",
                False,
            )

        # fan-out to timelines

        author_id = (
            instance.post.entity.id if instance.post.entity else instance.post.entity.id
        )
        current_user = instance.entity

        lock_key = f"chatterloop:bump_lock:{str(instance.post.post_id)}:{str(instance.entity.id)}:comment"

        if cache.add(lock_key, "active", timeout=1800):
            # Social bump: someone commented, so push the post into the feeds
            # of the people who follow THEM. Was the commenter's connections;
            # the feed is keyed on the follow graph now, and following is a
            # superset of connecting (both sides of a contact request
            # auto-follow), so connections still receive this - via the
            # follow edge that connecting created.
            follower_ids = get_follower_ids(current_user.id, limit=500)

            bulk_fanout_to_cache(
                follower_ids, {"id": instance.post.post_id, "author_id": author_id}
            )


@receiver(post_delete, sender=Comment)
def remove_comment_log(sender, instance, **kwargs):
    # Remove related EngagementLog entry when comment deleted
    # EngagementLog.objects.filter(
    #     reference_id=instance.comment_id, action="commented"
    # ).delete()

    logs = UserEngagementLog.objects.filter(
        user_id=str(instance.entity.id),
        activity_type="comment",
        target_type="post",
        target_id=str(instance.comment_id),
    )

    for log in logs:
        UserEngagementLog.objects.filter(
            user_id=log.user_id,
            activity_time=log.activity_time,
            log_id=log.log_id,
        ).delete()


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
    # EngagementLog.objects.filter(
    #     reference_id=instance.reaction_id, action="reacted"
    # ).delete()

    logs = UserEngagementLog.objects.filter(
        user_id=str(instance.entity.id),
        activity_type="react",
        target_type="post",
        target_id=str(instance.reaction_id),
    )

    for log in logs:
        UserEngagementLog.objects.filter(
            user_id=log.user_id,
            activity_time=log.activity_time,
            log_id=log.log_id,
        ).delete()


# Add Share Engagement Log to Server Express JS API
