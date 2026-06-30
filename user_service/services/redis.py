from django_redis import get_redis_connection
import json


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
    def update_feed_mode(cls, entity_id, finalized_mode):
        """
        Force-updates the current mode state in Redis. Useful for
        re-aligning the state when the view triggers a fallback path.
        """
        conn = cls.get_redis_connection()
        if conn:
            mode_key = f"chatterloop:feed:current_mode:{entity_id}"
            conn.setex(mode_key, 1800, finalized_mode)
