from django.db import transaction
from django.db.models import F

from interests.models import EntityInterestAffinity, InterestTrendingScore

# Mirrors newsfeed.helpers.query_functions.INTERACTION_WEIGHTS - kept as its
# own copy here (not imported from newsfeed) since interests must not
# depend on newsfeed (newsfeed already depends on interests, for
# Post.interests - importing back the other way would be circular).
INTERACTION_WEIGHTS = {
    "NEW_CONNECTION": 10.0,
    "SHARE": 7.0,
    "REPOST": 7.0,
    # Diary-only action: an entity explicitly labeling their own entry with
    # an interest is a stronger personal signal than reacting to someone
    # else's content, but not as strong as sharing/connecting.
    "DIARY_TAG": 5.0,
    "COMMENT": 4.0,
    "LIKE": 1.0,
    "VIEW": 0.1,
    "PROFILE_VISIT": 0.5,
}


def get_affinity_score(entity, interest):
    affinity = EntityInterestAffinity.objects.filter(entity=entity, interest=interest).first()
    return affinity.score if affinity is not None else 0.0


def bump_interest_affinity(entity_id, interest_ids, action, is_decrease):
    """
    Bumps both the per-entity affinity (personalization - "what does this
    entity actually care about") and the interest's global trending score
    (platform-wide - "what's popular right now") from the same engagement
    event, since both are driven by identical inputs and only differ in
    which axis they aggregate along.
    """
    weight = INTERACTION_WEIGHTS.get(action, 0.0)
    if weight == 0.0:
        return
    interest_ids = set(interest_ids)
    if not interest_ids:
        return

    delta = -weight if is_decrease else weight

    with transaction.atomic():
        for interest_id in interest_ids:
            obj, _ = EntityInterestAffinity.objects.select_for_update().get_or_create(
                entity_id=entity_id, interest_id=interest_id
            )
            EntityInterestAffinity.objects.filter(pk=obj.pk).update(score=F("score") + delta)

            trending, _ = InterestTrendingScore.objects.select_for_update().get_or_create(
                interest_id=interest_id
            )
            InterestTrendingScore.objects.filter(pk=trending.pk).update(score=F("score") + delta)
