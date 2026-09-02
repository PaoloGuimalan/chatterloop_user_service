from ..settings import (
    MONGODB_DB,
    MONGODB_CLUSTER_USER,
    MONGODB_CLUSTER_PASS,
    MONGODB_CLUSTER_HOST,
    MONGODB_URI,
)
import mongoengine


class MongoDBClient:
    _connection = None

    @classmethod
    def get_connection(cls):
        if cls._connection is None:
            cls._connection = mongoengine.connect(db=MONGODB_DB, host=cls.uri())
        return cls._connection

    @staticmethod
    def uri():
        """
        The connection string, assembled from the cluster parts unless
        MONGODB_URI overrides the whole thing.

        The assembled form is `mongodb+srv://`, which is what Atlas requires
        and which a plain mongod cannot satisfy - the scheme means "look these
        hosts up via SRV DNS records", and a container has none. Since
        user/apps.py connects during ready(), that made every manage.py
        command - migrate included - depend on reaching Atlas.

        Unset in deployed environments, so this returns exactly the URI it
        always did.
        """
        if MONGODB_URI:
            return MONGODB_URI
        return (
            f"mongodb+srv://{MONGODB_CLUSTER_USER}:{MONGODB_CLUSTER_PASS}"
            f"@{MONGODB_CLUSTER_HOST}/{MONGODB_DB}?retryWrites=true&w=majority"
        )
