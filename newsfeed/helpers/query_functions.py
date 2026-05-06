from ..models import Post, PostScore
from user.models import UserEngagementLog
from ..models import NewsfeedIndex
from django.utils.timezone import now, is_naive, make_aware, get_current_timezone
from django.utils.dateparse import parse_datetime
import uuid


def update_ranking_score(post_id, update_type, is_decrease):
    post_data = Post.objects.get(post_id=post_id)
    post_score = PostScore.objects.get(post=post_data)

    pending_bucket = (
        post_data.author_realm.id if post_data.author_realm else post_data.user.id
    )

    try:
        old_index = NewsfeedIndex.objects.filter(
            bucket=str(pending_bucket), post_id=str(post_id)
        ).first()
    except Exception as ex:
        print(ex)
        old_index = None

    reactions = post_score.likes_count

    reactions_total = reactions

    final_content_score = post_score.content_type_weight

    new_recent_update_boost = post_score.recent_update_boost

    if update_type == "react":
        if is_decrease:
            new_recent_update_boost -= 0.1
        else:
            new_recent_update_boost += 0.1
    elif update_type == "comment":
        if is_decrease:
            new_recent_update_boost -= 0.3
        else:
            new_recent_update_boost += 0.3
    elif update_type == "share":
        if is_decrease:
            new_recent_update_boost -= 0.5
        else:
            new_recent_update_boost += 0.5
    else:
        if is_decrease:
            new_recent_update_boost -= 0.1
        else:
            new_recent_update_boost += 0.1

    age_hours = (now() - post_data.date_posted).total_seconds() / 3600
    affinity_score = 1.0
    content_type_weight = final_content_score
    recent_update_boost = new_recent_update_boost
    comments_count = post_score.comments_count
    likes_count = reactions_total
    shares_count = post_score.shares_count

    base_engagement = 1

    weighted_engagement = (
        comments_count * 3 + likes_count * 1 + shares_count * 5 + base_engagement
    )
    decay_factor = (age_hours + 1) ** 0.5
    ranking_score = (
        (weighted_engagement / decay_factor)
        * affinity_score
        * content_type_weight
        * recent_update_boost
    )

    PostScore.objects.update_or_create(
        post=post_data,
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

    if old_index:
        NewsfeedIndex.objects.filter(
            bucket=old_index.bucket,
            ranking_score=old_index.ranking_score,
            latest_activity=old_index.latest_activity,
            post_id=old_index.post_id,
        ).delete()

    NewsfeedIndex.objects.create(
        bucket=str(pending_bucket),
        ranking_score=ranking_score,
        latest_activity=now(),
        post_id=str(post_id),
        author_id=str(pending_bucket),
    )


def save_viewcache_engagements(user, viewcache):
    try:
        if not viewcache:
            return []

        user_id = uuid.UUID(user.id) if isinstance(user.id, str) else user.id

        created = []
        for view in viewcache:
            pid = view["post_id"]
            poid = view["post_owner_id"]
            current_duration = view.get("duration", 0)

            if poid == user.id:
                return

            created_at = view.get("created_at")
            if isinstance(created_at, str):
                created_at = parse_datetime(created_at)
            if created_at and is_naive(created_at):
                created_at = make_aware(created_at, get_current_timezone())

            log = UserEngagementLog(
                user_id=user_id,
                activity_time=created_at,
                time_spent=float(current_duration),
                activity_type="view",
                target_type="post",
                target_id=str(pid),
            )
            log.save()
            created.append(log)

        return created
    except Exception as ex:
        print(ex)
