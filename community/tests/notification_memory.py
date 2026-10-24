"""Inert MongoEngine persistence boundary for notification behavior tests."""

from user.ext_models.mongomodels import Notification
from mongoengine.queryset import transform


class NotificationMemory:
    def __init__(self):
        self.rows = []
        # A plain function retains descriptor binding when installed on Document.
        self.save_document = lambda document, *args, **kwargs: self.save(document)

    def __call__(self, **query):
        return NotificationQuery(self, query)

    def save(self, document, *args, **kwargs):
        if document not in self.rows:
            self.rows.append(document)
        return document


class NotificationQuery:
    def __init__(self, store, query):
        self.store = store
        self.query = query

    def __iter__(self):
        for row in list(self.store.rows):
            if all(
                (getattr(row, key[:-4]) != value)
                if key.endswith("__ne")
                else getattr(row, key) == value
                for key, value in self.query.items()
            ):
                yield row

    def first(self):
        return next(iter(self), None)

    def filter(self, **query):
        return NotificationQuery(self.store, {**self.query, **query})

    def update_one(self, upsert=False, **updates):
        row = self.first()
        inserted = row is None
        if inserted:
            if not upsert:
                return 0
            row = Notification(**self.query)
            self.store.rows.append(row)
        self._update(row, updates, inserted)
        return 1

    def update(self, **updates):
        rows = list(self)
        for row in rows:
            self._update(row, updates, False)
        return len(rows)

    @staticmethod
    def _update(row, updates, inserted):
        # Validate the real field/operator serialization without a Mongo client.
        transform.update(Notification, **updates)
        for operation, value in updates.items():
            kind, field = operation.split("__", 1)
            if kind == "set" or (kind == "set_on_insert" and inserted):
                setattr(row, field, value)
            else:
                if kind != "set_on_insert":
                    raise AssertionError(f"Unsupported update: {operation}")

    def delete(self):
        rows = list(self)
        self.store.rows[:] = [row for row in self.store.rows if row not in rows]
        return len(rows)
