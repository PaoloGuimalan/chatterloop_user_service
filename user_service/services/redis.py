from django_redis import get_redis_connection
from django.db import transaction
import json
import logging

logger = logging.getLogger(__name__)

# The moderation service (chatterloop_services/moderation_service) writes this
# key with a 30s TTL and refreshes it every 10s, so its presence means an
# instance is alive RIGHT NOW rather than "was started at some point" - a hard
# kill leaves no stale key behind, it just lapses.
MODERATION_PRESENCE_KEY = "chatterloop:moderation_service"


class RedisPubSubClient:
    _redis_conn = None

    @classmethod
    def get_redis_connection(cls):
        if cls._redis_conn is None:
            cls._redis_conn = get_redis_connection("default")
        return cls._redis_conn

    @classmethod
    def publish(cls, channel, message):
        conn = cls.get_redis_connection()
        conn.publish(channel, message)

    @classmethod
    def publish_json(cls, channel, data):
        redis_conn = cls.get_redis_connection()
        message = json.dumps(data)  # Serialize Python dict to JSON string
        redis_conn.publish(channel, message)

    @classmethod
    def publish_json_on_commit(cls, channel, data):
        """
        publish_json deferred until the surrounding transaction COMMITS.

        Use this for any event that makes the receiver come back and read the
        rows this request is writing. Publishing inline from inside
        `with transaction.atomic()` is a race the receiver usually wins: the
        SSE frame leaves immediately, the client refetches on a different
        connection, and it reads the PRE-change rows because the writer has
        not committed yet. Accepting a contact request looked exactly like
        this - the requester's page reloaded and showed the old state.

        Outside a transaction, Django runs the callable immediately, so this
        is always safe to use in place of publish_json.

        `data` is bound as a default argument rather than captured by the
        closure, so publishing in a loop cannot end up sending the last
        iteration's payload to every channel.
        """
        transaction.on_commit(
            lambda channel=channel, data=data: cls.publish_json(channel, data)
        )

    @classmethod
    def subscribe(cls, channel):
        conn = cls.get_redis_connection()
        pubsub = conn.pubsub()
        pubsub.subscribe(channel)
        return pubsub

    @classmethod
    def is_unique_nonce(cls, user_id, timestamp, random_str):
        conn = cls.get_redis_connection()
        if conn:
            redis_key = f"nonce:{user_id}:{timestamp}:{random_str}"
            result = conn.set(redis_key, "1", nx=True, ex=60)

            return result is True

        return False

    @classmethod
    def acquire_email_cooldown(cls, action, from_entity_id, to_entity_id, ttl=1800):
        """
        Generic per-action email rate-limiter, e.g. stops rapid add/cancel/
        re-add or repeated-poke clicks from spamming the same recipient.
        Returns True the first time it's called for a given action/pair
        within the cooldown window (and the email should be sent), False
        otherwise. If Redis is unavailable, fails open so the email still
        goes out.
        """
        conn = cls.get_redis_connection()
        if not conn:
            return True

        cooldown_key = (
            f"chatterloop:email:{action}_cooldown:{from_entity_id}:{to_entity_id}"
        )
        result = conn.set(cooldown_key, "1", nx=True, ex=ttl)

        return result is True

    @classmethod
    def get_and_toggle_feed_mode(cls, entity_id, fallback_mode="friends"):
        """
        Determines the feed mode for the current request by reading the
        previous state from Redis, automatically toggling it, and saving it.
        """
        conn = cls.get_redis_connection()
        if not conn:
            return fallback_mode

        mode_key = f"chatterloop:feed:current_mode:{entity_id}"

        # 1. Read the previous mode
        last_mode = conn.get(mode_key)

        if last_mode:
            # Decode the bytes from Redis to a string
            last_mode_str = (
                last_mode.decode() if isinstance(last_mode, bytes) else str(last_mode)
            )
            current_mode = "trending" if last_mode_str == "friends" else "friends"
        else:
            current_mode = fallback_mode

        # 2. Instantly save the new mode so the next request flips automatically
        # Set a 30-minute expiration (1800 seconds) to save Redis memory
        conn.setex(mode_key, 1800, current_mode)

        return current_mode

    @classmethod
    def is_moderation_service_online(cls):
        """
        Whether the moderation service is available to receive work.

        Gates the content_tagging publish: when this is False the caller
        publishes NOTHING and the moderation service's database scour picks the
        content up on its next start. That is the designed path, not a degraded
        one - which is why skipping is safe and why this never raises.

        FAILS CLOSED, unlike acquire_email_cooldown above, which deliberately
        fails open so a Redis outage still lets the email go out. The asymmetry
        is the point: a missed cooldown sends one extra email, whereas assuming
        the moderation service is up publishes into a queue that may have no
        consumer - and the scour would then never revisit that content, because
        from its side the work was already handed off. Skipping loses nothing;
        publishing into the void loses the content.

        Only existence is checked, never the value. The value is diagnostic
        (instance, version, uptime); the moment this parsed it, adding a field
        there would become a cross-service change.
        """
        try:
            conn = cls.get_redis_connection()
            if not conn:
                return False

            return bool(conn.exists(MODERATION_PRESENCE_KEY))
        except Exception as err:
            # Deliberately broad: redis-py surfaces connection errors, timeouts
            # and auth failures as unrelated types, and none of them is a
            # reason to fail the request that triggered this.
            logger.warning(
                "moderation: availability check failed, treating as offline: %s", err
            )
            return False

    @classmethod
    def update_feed_mode(cls, entity_id, finalized_mode):
        """
        Force-updates the current mode state in Redis. Useful for
        re-aligning the state when the view triggers a fallback path.
        """
        conn = cls.get_redis_connection()
        if conn:
            mode_key = f"chatterloop:feed:current_mode:{entity_id}"
            conn.setex(mode_key, 1800, finalized_mode)
