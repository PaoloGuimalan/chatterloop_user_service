from newsfeed.models import Post, PostScore, ActivityCount, PreviewCount, PostReference
from django.utils.timezone import now

posts = Post.objects.all()

for post_data in posts:
    comment = ActivityCount.objects.get(post=post_data, count_type="comment")
    share = ActivityCount.objects.get(post=post_data, count_type="share")
    reactions = PreviewCount.objects.filter(post=post_data)
    references = PostReference.objects.filter(post=post_data)

    reactions_total = sum([reaction.count for reaction in reactions])

    content_t_m = 1.0

    if len(references) > 0:
        for reference in references:
            if reference.reference_media_type == "image":
                content_t_m += 6.5
            elif reference.reference_media_type == "video":
                content_t_m += 8.5
            else:
                content_t_m += 2.0
    else:
        content_t_m += 4.0

    final_content_score = content_t_m / (len(references) + 1)

    age_hours = (now() - post_data.date_posted).total_seconds() / 3600
    affinity_score = 1.0
    content_type_weight = final_content_score
    recent_update_boost = 1.0
    comments_count = comment.count
    likes_count = reactions_total
    shares_count = share.count

    weighted_engagement = comments_count * 3 + likes_count * 1 + shares_count * 5
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
