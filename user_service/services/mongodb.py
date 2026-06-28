from ..settings import (
    MONGODB_DB,
    MONGODB_CLUSTER_USER,
    MONGODB_CLUSTER_PASS,
    MONGODB_CLUSTER_HOST,
)
import os
import mongoengine


class MongoDBClient:
    _connection = None

    @classmethod
    def get_connection(cls):
        if cls._connection is None:
            # Local-dev override: set MONGODB_URI to a local mongo (e.g.
            # mongodb://localhost:27017/chatterloop). Falls back to the cloud cluster.
            host = os.getenv("MONGODB_URI") or (
                f"mongodb+srv://{MONGODB_CLUSTER_USER}:{MONGODB_CLUSTER_PASS}@{MONGODB_CLUSTER_HOST}/{MONGODB_DB}?retryWrites=true&w=majority"
            )
            cls._connection = mongoengine.connect(db=MONGODB_DB, host=host)
        return cls._connection
