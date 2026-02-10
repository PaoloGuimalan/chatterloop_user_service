from django.db.models.signals import post_save, post_delete
from django.utils.timezone import now
from django.dispatch import receiver
from .models import Emoji, Post, PreviewCount, PostReference, PostScore, EngagementLog, Comment, Reaction
import uuid


@receiver(post_save, sender=Emoji)
def create_post_preview_counts(sender, instance, created, **kwargs):
    if created:
        # For each existing post, create a PreviewCount with count=0 for the new emoji
        posts = Post.objects.all()
        post_preview_counts = [
            PreviewCount(
                preview_id=str(uuid.uuid4()), post=post, emoji=instance, count=0
            )
            for post in posts
        ]
        PreviewCount.objects.bulk_create(post_preview_counts)


@receiver(post_save, sender=Post)
def create_preview_counts_for_new_post(sender, instance, created, **kwargs):
    if created:
        emojis = Emoji.objects.all()
        preview_counts = [
            PreviewCount(
                preview_id=str(uuid.uuid4()), post=instance, emoji=emoji, count=0
            )
            for emoji in emojis
        ]
        PreviewCount.objects.bulk_create(preview_counts)
        references = PostReference.objects.filter(post=instance)

        # ActivityCount.objects.create(
        #     count_id=str(uuid.uuid4()), post=instance, count_type="comment", count=0
        # )

        # ActivityCount.objects.create(
        #     count_id=str(uuid.uuid4()), post=instance, count_type="share", count=0
        # )

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
        EngagementLog.objects.create(
            post=instance.post,
            user=instance.user,
            action='commented',           # → reference_id = comment.comment_id
            reference_id=instance.comment_id
        )

@receiver(post_delete, sender=Comment)
def remove_comment_log(sender, instance, **kwargs):
    # Remove related EngagementLog entry when comment deleted
    EngagementLog.objects.filter(
        reference_id=instance.comment_id,
        action='commented'
    ).delete()

@receiver(post_save, sender=Reaction)
def log_reaction_action(sender, instance, created, **kwargs):
    if created:
        EngagementLog.objects.create(
            post=instance.post,
            user=instance.user,
            action='reacted',             # → reference_id = reaction.reaction_id  
            reference_id=instance.reaction_id
        )

@receiver(post_delete, sender=Reaction)
def remove_reaction_log(sender, instance, **kwargs):
    EngagementLog.objects.filter(
        reference_id=instance.reaction_id,
        action='reacted'
    ).delete()

# Add Share Engagement Log to Server Express JS API
