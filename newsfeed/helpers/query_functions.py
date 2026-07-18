from ..models import Post, PostScore
from user.models import UserEngagementLog, Connection, Account
from community.models import RealmFollow
from ..models import NewsfeedIndex, TrendingPool
from cassandra.cqlengine.query import BatchQuery
from django.utils.timezone import now, is_naive, make_aware, get_current_timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from django.db.models import Q, F
from user.services.connections import ConnectionHelpers
from interests.services.affinity import bump_interest_affinity
from interests.services.interest_resolver import IMPLICIT_GRANT_THRESHOLD
from interests.models import EntityInterest, EntityInterestAffinity
from entity.permissions import PermissionEffect
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


def save_viewcache_engagements(entity, viewcache):
    try:
        if not viewcache:
            return []

        user_id = uuid.UUID(entity.id) if isinstance(entity.id, str) else entity.id

        logs_to_create = []
        post_ids_to_clean = []
        viewed_post_ids = [str(view["post_id"]) for view in viewcache]
        # One query for every viewed post's interests, keyed by post_id -
        # avoids an N+1 per view when bumping affinity below.
        interests_by_post_id = {}
        for pid, interest_id in Post.objects.filter(
            post_id__in=viewed_post_ids
        ).values_list("post_id", "interests__id"):
            if interest_id is not None:
                interests_by_post_id.setdefault(pid, []).append(interest_id)

        for view in viewcache:
            pid = view["post_id"]
            poid = view["post_owner_id"]
            current_duration = view.get("duration", 0)

            post_ids_to_clean.append(str(pid))

            bump_interest_affinity(entity.id, interests_by_post_id.get(str(pid), []), "VIEW", False)

            if str(poid) != str(entity.id):
                created_at = view.get("created_at")
                if isinstance(created_at, str):
                    created_at = parse_datetime(created_at)
                if created_at and is_naive(created_at):
                    created_at = make_aware(created_at, get_current_timezone())

                log_instance = UserEngagementLog(
                    user_id=user_id,
                    activity_time=created_at,
                    time_spent=float(current_duration),
                    activity_type="view",
                    target_type="post",
                    target_id=str(pid),
                )
                logs_to_create.append(log_instance)

        with BatchQuery() as b:

            for log in logs_to_create:
                log.batch(b).save()

            if post_ids_to_clean:
                rows_to_delete = NewsfeedIndex.objects.filter(
                    bucket=str(user_id), post_id__in=post_ids_to_clean
                )
                for row in rows_to_delete:
                    row.batch(b).delete()

        return logs_to_create
    except Exception as ex:
        print(f"Error saving viewcache metrics: {ex}")
        return []


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
            Q(action_by__id=actor_id, involved_entity__id=receiver_id)
            | Q(action_by__id=receiver_id, involved_entity__id=actor_id)
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
            follower_id=actor_id, realm__entity_id=receiver_id
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
            activity_type__in=["comment", "share"],
            target_id__in=candidate_pids_str,
        )

        for log in logs:
            pid = log.target_id
            ts = log.activity_time

            if pid not in latest_social_map or ts > latest_social_map[pid]:
                latest_social_map[pid] = ts

    return latest_social_map


def backfill_new_friend_feed(viewer_id, new_friend_id):
    user = Account.objects.get(entity_id=viewer_id)

    candidate_posts = Post.objects.filter(entity__id=str(new_friend_id))[:50]
    candidate_pids = [str(p.post_id) for p in candidate_posts]

    view_logs = UserEngagementLog.objects.filter(
        user_id=viewer_id,
        activity_type="view",
        target_type="post",
        target_id__in=candidate_pids,
    )

    user_connections = ConnectionHelpers(user.entity)
    mutual_friends = user_connections.get_mutual_connections(new_friend_id)

    mutual_friend_engagements = {}
    mutual_friend_engagements = get_latest_mutual_engagements(
        mutual_friends, candidate_pids
    )

    for log in view_logs:
        if log.target_id not in mutual_friend_engagements:
            mutual_friend_engagements[log.target_id] = log.activity_time

    b = BatchQuery()

    inserted_count = 0
    for post in candidate_posts:
        pid = str(post.post_id)
        final_timestamp = post.date_posted
        should_insert = False

        if pid not in mutual_friend_engagements:
            should_insert = True
        else:
            bump_ts = mutual_friend_engagements.get(pid, None)

            if bump_ts and bump_ts > mutual_friend_engagements[pid]:
                final_timestamp = bump_ts
                should_insert = True

        if should_insert:
            NewsfeedIndex.batch(b).create(
                bucket=str(viewer_id),
                post_id=pid,
                created_at=now(),
                author_id=str(new_friend_id),
            )
            inserted_count += 1

    if inserted_count > 0:
        b.execute()
        print(
            f"Successfully executed batch for {inserted_count} posts | {viewer_id}:{new_friend_id}"
        )


def fetch_friends_posts(entity_id, page_size=10):
    newsfeed_queryset = (
        NewsfeedIndex.objects.filter(bucket=str(entity_id))
        .limit(int(page_size))
        .values_list("post_id", flat=True)
    )
    return list(newsfeed_queryset)


# TrendingPool.category is the partition key, and fetch_trending_posts
# queries it with category__in=user_interests - this cluster's configured
# hard limit is 20 partition keys per query ("Select query cannot be
# completed because it selects N partitions keys - more than the maximum
# allowed 20"), confirmed directly from the server error, not assumed.
# Reserve 1 slot for the always-included "global" fallback below.
MAX_TRENDING_CATEGORIES = 19


def resolved_interest_categories(entity):
    """
    Replaces the previously-hardcoded ["global"] placeholder passed to
    fetch_trending_posts: explicitly granted interests, plus any the entity
    has a high implicit affinity for (see interests.services.affinity),
    plus "global" itself kept as an always-included fallback bucket so the
    pool never comes back completely empty for a brand new entity with no
    signal yet.

    Capped at MAX_TRENDING_CATEGORIES total (see constant above) - an
    entity can accumulate far more than 25 granted/high-affinity interests
    over time (e.g. via diary tagging), which would otherwise blow up
    TrendingPool's category__in query. Explicit grants are a stronger
    signal than implicit affinity, so grants fill the cap first; implicit
    interests only fill whatever's left, highest-scored first.
    """
    granted = list(
        EntityInterest.objects.filter(entity=entity, effect=PermissionEffect.GRANT)
        .values_list("interest__normalized_name", flat=True)[:MAX_TRENDING_CATEGORIES]
    )
    remaining_slots = max(MAX_TRENDING_CATEGORIES - len(granted), 0)
    implicit = list(
        EntityInterestAffinity.objects.filter(
            entity=entity, score__gte=IMPLICIT_GRANT_THRESHOLD
        )
        .order_by("-score")
        .values_list("interest__normalized_name", flat=True)[:remaining_slots]
    )
    return list(set(granted) | set(implicit) | {"global"})


def fetch_trending_posts(
    entity_id, page_size=10, candidate_limit=100, user_interests=[]
):
    # change for later when trending pool is widely used, make population pool flexible to seen posts
    entity_uuid = (
        uuid.UUID(str(entity_id)) if not isinstance(entity_id, uuid.UUID) else entity_id
    )

    # Defensive cap independent of the caller: category__in is a Cassandra/
    # Scylla query against TrendingPool's partition key, which rejects more
    # than 25 IN values regardless of how user_interests was built.
    trending_queryset = (
        TrendingPool.objects.filter(category__in=list(user_interests)[:MAX_TRENDING_CATEGORIES + 1])
        .limit(int(candidate_limit))
        .values_list("post_id", flat=True)
    )
    trending_pids = list(trending_queryset)

    if not trending_pids:
        return []

    seen_logs = UserEngagementLog.objects.filter(
        user_id=entity_uuid, activity_type="view", target_id__in=trending_pids
    )
    seen_pids = {str(log.target_id) for log in seen_logs}
    return [pid for pid in trending_pids if pid not in seen_pids][: int(page_size)]
