"""
Publishing side of the Go worker_service.

No Celery. Work is handed over as a plain JSON message on a named, durable
queue, published to the default exchange with the queue name as the routing
key - exactly what worker_service/internal/services/rabbitmq/rabbitmq.go does
in its Publish(), so both services speak the same protocol.

kombu is used as the AMQP client rather than pika. It is already installed (as
a Celery dependency, but it is a standalone messaging library and using it
implies nothing about Celery), and its producer pool is safe to share across
gunicorn's threads, which pika's BlockingConnection is not.

Publishing is BEST-EFFORT and never raises. A broker outage must degrade a
ranking recalculation, not fail the request that triggered it.
"""

import logging
import threading
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.db import transaction
from kombu import Connection, Queue

logger = logging.getLogger(__name__)

# Serialises publishes within a process so one connection can be shared.
#
# kombu's producer pool was the obvious choice and is the wrong one here: it
# pools CONNECTIONS, not channels, so concurrent publishes each opened their own
# AMQP connection and the pool held them open - a broker showing seven
# connections from one host, each with a single idle channel.
#
# These messages are a few hundred bytes on an already-established connection,
# so the contention this lock introduces is microseconds; a hosted broker's
# connection limit is the scarcer resource. One connection per gunicorn worker.
_publish_lock = threading.Lock()


class Queues:
    """
    Queue names, one per listener registered in the worker's
    internal/startup/init.go. Referencing a constant rather than a literal is
    what makes a typo an AttributeError here instead of a message published to
    a queue nobody consumes.
    """

    UPDATE_RANKING_SCORE = "update_ranking_score"
    SAVE_VIEWCACHE_ENGAGEMENTS = "save_viewcache_engagements"
    BUMP_INTEREST_AFFINITY = "bump_interest_affinity"
    INTERACTION_SCORE_BUMP = "interaction_score_bump"
    FOLLOWER_INTERACTION_SCORE_BUMP = "follower_interaction_score_bump"
    CREATE_POST_SCORE_FOR_NEW_POST = "create_post_score_for_new_post"
    BULK_FANOUT_TO_CACHE = "bulk_fanout_to_cache"
    BACKFILL_NEW_FRIEND_FEED = "backfill_new_friend_feed"
    REMOVE_FEED_ON_UNFRIEND = "remove_feed_on_unfriend"
    REMOVE_ENGAGEMENT_LOG = "remove_engagement_log"
    SEND_EMAIL = "send_email"


def _normalize(value):
    """
    Reduce a payload to types the Go handlers can decode.

    Done here rather than left to kombu's serializer on purpose: recent kombu
    encodes datetimes and UUIDs as tagged objects like
    {"__type__": "datetime", "__value__": ...}, which encoding/json would hand
    the worker as an empty struct field. Converting first means the body is
    ordinary JSON with no framework markers in it.

    Datetimes go out as RFC3339, matching what every handler parses.
    """
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class RabbitMQClient:
    """
    Mirrors RedisPubSubClient's shape: classmethods over one lazily-built
    shared connection.
    """

    _connection = None
    _producer = None
    # Queue names already declared on this connection. Declaring is idempotent
    # but costs a round trip, and these queues are durable - so once per process
    # is enough, and it survives a reconnect because the queue outlives it.
    _declared = set()

    @classmethod
    def get_connection(cls):
        if cls._connection is None:
            if not settings.RABBITMQ_URL:
                return None
            cls._connection = Connection(
                settings.RABBITMQ_URL,
                connect_timeout=settings.RABBITMQ_CONNECT_TIMEOUT,
            )
        return cls._connection

    @classmethod
    def _reset(cls):
        """Drop the cached producer so the next publish rebuilds it."""
        cls._producer = None
        cls._declared = set()

    @classmethod
    def publish(cls, queue, payload):
        """
        Hand one job to the worker. Returns True when the broker accepted it.

        The queue is declared durable on every publish, same as the Go side
        does, so whichever service starts first the queue exists and messages
        survive a broker restart.
        """
        connection = cls.get_connection()
        if connection is None:
            logger.warning(
                "rabbitmq: dropping %s, RABBITMQ_HOST is not configured", queue
            )
            return False

        body = _normalize(payload)

        try:
            with _publish_lock:
                if cls._producer is None:
                    cls._producer = connection.Producer()

                # ensure() re-opens the connection and revives the channel when
                # it has gone away underneath us - which it will, since a hosted
                # broker reaps idle connections and nothing here sends
                # heartbeats between requests.
                publish = connection.ensure(
                    cls._producer,
                    cls._producer.publish,
                    max_retries=settings.RABBITMQ_PUBLISH_MAX_RETRIES,
                    interval_start=0,
                    interval_step=0.2,
                    interval_max=1,
                )

                declare = [] if queue in cls._declared else [Queue(queue, durable=True)]

                publish(
                    body,
                    exchange="",
                    routing_key=queue,
                    serializer="json",
                    # 2 = persistent, so a queued job outlives a broker restart.
                    delivery_mode=2,
                    declare=declare,
                )

                cls._declared.add(queue)
            return True
        except Exception as err:
            # Deliberately broad: kombu surfaces socket errors, timeouts and
            # AMQP channel errors as unrelated types, and none of them is a
            # reason to fail the request that triggered this.
            logger.error("rabbitmq: failed to publish to %s: %s", queue, err)
            cls._reset()
            return False

    @classmethod
    def publish_on_commit(cls, queue, payload):
        """
        publish() deferred until the surrounding transaction COMMITS.

        Prefer this everywhere. The worker reads the rows the current request
        is writing, and publishing from inside `with transaction.atomic()` is a
        race it usually wins: the job is picked up on another connection before
        the writer commits, so it recalculates from pre-change rows. The same
        reasoning - and the same fix - as RedisPubSubClient.publish_json_on_commit.

        Outside a transaction, Django runs the callable immediately, so this is
        always safe to use in place of publish().

        Arguments are bound as defaults rather than captured by the closure, so
        publishing in a loop cannot send the last iteration's payload every time.
        """
        transaction.on_commit(
            lambda queue=queue, payload=payload: cls.publish(queue, payload)
        )

    @classmethod
    def close(cls):
        cls._reset()
        if cls._connection is not None:
            try:
                cls._connection.release()
            except Exception:
                pass
            cls._connection = None
