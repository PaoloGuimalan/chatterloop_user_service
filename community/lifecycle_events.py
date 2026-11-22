"""Postcommit invalidation over current event channels; failures never undo intent."""
import logging

from django.utils.timezone import now

from community.access import descendant_realms
from community.lifecycle_state import lifecycle_state
from community.models import Member, Realm
from user_service.services.redis import RedisPubSubClient
from user_service.services.rabbitmq import Queues, RabbitMQClient

logger = logging.getLogger(__name__)


def dispatch_lifecycle(realm_id, state):
    realm = Realm.objects.filter(pk=realm_id).first()
    if realm is None:
        return True
    # Read current state: a queued old hint must not undo a later restoration.
    state = str(lifecycle_state(realm))
    scope = descendant_realms(realm_id)
    recipients = set(map(str, Member.objects.filter(realm_id__in=scope.values('pk')).values_list('entity_id', flat=True)))
    recipients.update(map(str, scope.values_list('entity_id', flat=True)))
    signal = dict(realm_id=str(realm_id), state=state, occurred_at=now().isoformat())
    frame = {'logType': None, 'pod': 'podless', 'event': 'realm_lifecycle_changed',
             'message': {'auth': True, 'status': True, 'result': signal}, 'dateTime': signal['occurred_at']}
    successful = True
    if realm.deletion_requested_at is not None:
        if not RabbitMQClient.publish(Queues.REALM_DELETION, {
            'realm_id': str(realm.pk), 'deletion_requested_at': realm.deletion_requested_at,
        }):
            successful = False
            logger.warning('Realm deletion publish failed for %s; reconciliation will retry', realm_id)
    for channel, payload in [
        *((f'events_{recipient}', frame) for recipient in recipients),
        ('SUB_realm_lifecycle', {'event': 'realm-lifecycle-relay', 'message': {'realm_id': str(realm_id)}}),
    ]:
        try:
            RedisPubSubClient.publish_json(channel, payload)
        except Exception:
            successful = False
            logger.warning('Could not publish realm lifecycle invalidation for %s', realm_id, exc_info=True)
    return successful
