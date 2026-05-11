from ..models import Post, PostScore
from user.models import UserEngagementLog, Connection
from community.models import RealmFollow
from ..models import NewsfeedIndex
from cassandra.cqlengine.query import BatchQuery
from django.utils.timezone import now, is_naive, make_aware, get_current_timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from django.db.models import Q, F
import uuid


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

            if poid != user.id:
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

            # 1. Find all timestamps for this post in the user's bucket
            rows = NewsfeedIndex.objects.filter(bucket=str(user_id), post_id=pid)

            # 2. Delete each specific version
            for row in rows:
                row.delete()

        return created
    except Exception as ex:
        print(ex)


def bulk_fanout_to_cache(connections_list, post_data):
    with BatchQuery() as b:
        for follower_id in connections_list:
            NewsfeedIndex.batch(b).create(
                bucket=str(follower_id),
                post_id=str(post_data["id"]),
                created_at=now(),
                author_id=str(post_data["author_id"]),
            )


def interaction_score_bump(actor_id, receiver_id, action, is_decrease):
    if actor_id == receiver_id:
        return

    INTERACTION_WEIGHTS = {
        "NEW_CONNECTION": 10.0,
        "SHARE": 7.0,
        "REPOST": 7.0,
        "COMMENT": 4.0,
        "LIKE": 1.0,
        "VIEW": 0.1,
        "PROFILE_VISIT": 0.5,
    }

    weight = INTERACTION_WEIGHTS.get(action, 0.0)

    with transaction.atomic():
        existing_connection_query = Connection.objects.filter(
            Q(action_by__id=actor_id, involved_user__id=receiver_id)
            | Q(action_by__id=receiver_id, involved_user__id=actor_id)
        )

        connection_ids = []

        for connection in existing_connection_query:
            connection_ids.append(connection.connection_id)

        connection_ids = list(set(connection_ids))

        for id in connection_ids:
            main_ids = []

            connections_to_update = Connection.objects.filter(connection_id=id)

            # 2. Loop and update
            for connection in connections_to_update:
                main_ids.append(connection.id)

            final_updates = Connection.objects.select_for_update().filter(
                id__in=main_ids
            )

            final_updates.update(
                interaction_score=(
                    F("interaction_score") - weight
                    if is_decrease
                    else F("interaction_score") + weight
                ),
                last_interaction_at=now(),
            )


def follower_interaction_score_bump(actor_id, receiver_id, action, is_decrease):
    if not receiver_id:
        return

    INTERACTION_WEIGHTS = {
        "NEW_CONNECTION": 10.0,
        "SHARE": 7.0,
        "REPOST": 7.0,
        "COMMENT": 4.0,
        "LIKE": 1.0,
        "VIEW": 0.1,
        "PROFILE_VISIT": 0.5,
    }

    weight = INTERACTION_WEIGHTS.get(action, 0.0)

    with transaction.atomic():
        follower_log = RealmFollow.objects.select_for_update().filter(
            follower_id=actor_id, realm_id=receiver_id
        )

        follower_log.update(
            interaction_score=(
                F("interaction_score") - weight
                if is_decrease
                else F("interaction_score") + weight
            ),
            last_interaction_at=now(),
        )


def remove_feed_on_unfriend(actor_id, author_id):
    rows = NewsfeedIndex.objects.filter(
        bucket=str(actor_id), author_id=author_id, type="fanout"
    )

    for row in rows:
        row.delete()

def get_latest_mutual_engagements(mutual_friend_ids, candidate_pids):
    latest_social_map = {}
    candidate_pids_str = [str(pid) for pid in candidate_pids]

    for mf_id in mutual_friend_ids:
        logs = UserEngagementLog.objects.filter(
            user_id=mf_id,
            activity_type__in=['comment', 'share'],
            target_id__in=candidate_pids_str
        )

        for log in logs:
            pid = log.target_id
            ts = log.activity_time
            
            if pid not in latest_social_map or ts > latest_social_map[pid]:
                latest_social_map[pid] = ts

    return latest_social_map

