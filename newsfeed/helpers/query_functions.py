from ..models import Post, PostScore, ActivityCount, PreviewCount, PostReference
from django.utils.timezone import now


def update_ranking_score(post_id, update_type, is_decrease):
    post_data = Post.objects.get(post_id=post_id)

    post_score = PostScore.objects.get(post=post_data)
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
