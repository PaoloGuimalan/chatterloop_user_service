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
