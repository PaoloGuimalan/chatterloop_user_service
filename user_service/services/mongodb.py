from ..settings import (
    MONGODB_DB,
    MONGODB_CLUSTER_USER,
    MONGODB_CLUSTER_PASS,
    MONGODB_CLUSTER_HOST,
)
import mongoengine


class MongoDBClient:
    _connection = None

    @classmethod
    def get_connection(cls):
        if cls._connection is None:
            cls._connection = mongoengine.connect(
                db=MONGODB_DB,
                host=f"mongodb+srv://{MONGODB_CLUSTER_USER}:{MONGODB_CLUSTER_PASS}@{MONGODB_CLUSTER_HOST}/{MONGODB_DB}?retryWrites=true&w=majority",
            )
        return cls._connection
