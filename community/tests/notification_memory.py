"""Inert MongoEngine persistence boundary for notification behavior tests."""

from user.ext_models.mongomodels import Notification
from mongoengine import NotUniqueError
from mongoengine.queryset import transform
from types import SimpleNamespace


class NotificationMemory:
    def __init__(self, model=Notification):
        self.model = model
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
        self.ordering = ()
        self.offset = 0
        self.maximum = None

    def __iter__(self):
        rows = [
            row
            for row in list(self.store.rows)
            if all(self.matches(row, key, value) for key, value in self.query.items())
        ]
        for field in reversed(self.ordering):
            rows.sort(
                key=lambda row: str(self.field(row, field.lstrip("-")) or ""),
                reverse=field.startswith("-"),
            )
        stop = None if self.maximum is None else self.offset + self.maximum
        yield from rows[self.offset : stop]

    @staticmethod
    def field(row, path):
        for key in path.split("__"):
            row = getattr(row, key, None)
        return row

    @classmethod
    def matches(cls, row, key, value):
        if key.endswith("__exists"):
            return (cls.field(row, key[:-8]) is not None) == value
        if key.endswith("__ne"):
            return cls.field(row, key[:-4]) != value
        if key.endswith("__lte"):
            return cls.field(row, key[:-5]) <= value
        if key.endswith("__in"):
            return cls.field(row, key[:-4]) in value
        return cls.field(row, key) == value

    def order_by(self, *fields):
        self.ordering = fields
        return self

    def skip(self, count):
        self.offset = count
        return self

    def limit(self, count):
        self.maximum = count
        return self

    def count(self):
        return len(list(self))

    def first(self):
        return next(iter(self), None)

    def distinct(self, field):
        return list({self.field(row, field) for row in self})

    def filter(self, **query):
        return NotificationQuery(self.store, {**self.query, **query})

    def update_one(self, upsert=False, full_result=False, **updates):
        row = self.first()
        inserted = row is None
        if inserted:
            if not upsert:
                return 0
            if self.query.get("id") is not None and any(
                item.pk == self.query["id"] for item in self.store.rows
            ):
                raise NotUniqueError("Duplicate notification slot")
            row = self.store.model(**{key: value for key, value in self.query.items() if "__" not in key})
            self.store.rows.append(row)
        before = row.to_mongo().to_dict()
        self._update(row, updates, inserted)
        return (
            SimpleNamespace(
                upserted_id="inserted" if inserted else None,
                modified_count=int(not inserted and before != row.to_mongo().to_dict()),
            )
            if full_result
            else 1
        )

    def update(self, **updates):
        rows = list(self)
        for row in rows:
            self._update(row, updates, False)
        return len(rows)

    def _update(self, row, updates, inserted):
        # Validate the real field/operator serialization without a Mongo client.
        if "__raw__" in updates:
            for stage in updates["__raw__"]:
                transform.update(self.store.model, __raw__=stage)
                values = {
                    key: NotificationQuery.expression(row, value)
                    for key, value in stage["$set"].items()
                }
                for key, value in values.items():
                    setattr(row, key, self.store.model._fields[key].to_python(value))
            return
        transform.update(self.store.model, **updates)
        for operation, value in updates.items():
            kind, field = operation.split("__", 1)
            if kind == "set" or (kind == "set_on_insert" and inserted):
                setattr(row, field, value)
            elif kind == "inc":
                setattr(row, field, (getattr(row, field, None) or 0) + value)
            else:
                if kind != "set_on_insert":
                    raise AssertionError(f"Unsupported update: {operation}")

    @classmethod
    def expression(cls, row, value):
        if isinstance(value, str) and value.startswith("$"):
            return getattr(row, value[1:], None)
        if not isinstance(value, dict):
            return value
        if "$literal" in value:
            return value["$literal"]
        operation, arguments = next(iter(value.items()))
        args = [cls.expression(row, item) for item in arguments]
        if operation == "$ifNull":
            return args[0] if args[0] is not None else args[1]
        if operation == "$add":
            return sum(args)
        if operation == "$eq":
            return args[0] == args[1]
        if operation == "$cond":
            return args[1] if args[0] else args[2]
        raise AssertionError(f"Unsupported expression: {operation}")

    def delete(self):
        rows = list(self)
        self.store.rows[:] = [row for row in self.store.rows if row not in rows]
        return len(rows)
