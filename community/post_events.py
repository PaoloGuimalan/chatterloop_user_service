"""Content-free invalidation over the existing per-entity event streams."""
import logging
from datetime import datetime
from community.models import Member
from community.access import effective_realm_access
from community.posts import community_for_post
from newsfeed.models import Post
from user_service.services.redis import RedisPubSubClient

logger = logging.getLogger(__name__)


def notify_post_state_changed(post_id):
    try:
        post = Post.objects.prefetch_related('tagging__entity__realms').filter(pk=post_id).first()
        realm = community_for_post(post) if post is not None else None
        if realm is None or not effective_realm_access(realm).allowed:
            return
        recipients = set(map(str, Member.objects.filter(realm=realm).values_list('entity_id', flat=True)))
        recipients.add(str(post.entity_id))
        signal = dict(realm_id=str(realm.pk), target_id=str(post.pk), approval=post.approval,
            content_version=(post.updated_at or post.date_posted).isoformat(), is_archived=post.is_archived)
        for recipient in recipients:
            RedisPubSubClient.publish_json(f'events_{recipient}', {
                'logType': None, 'pod': 'podless', 'event': 'community_posts_changed',
                'message': {'auth': True, 'status': True, 'result': signal},
                'dateTime': datetime.now().astimezone().isoformat()})
    except Exception:
        logger.exception('Could not invalidate community post %s', post_id)
