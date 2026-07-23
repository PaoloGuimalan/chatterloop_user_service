from user.serializers import ConnectionSerializer
from ..models import Connection
from entity.models import Entity
from entity.utils import entity_side_is_visible
from django.db.models import Q, F


class ConnectionHelpers:
    def __init__(self, entity):
        self.entity = entity

    def get_connections(self):
        entity = self.entity
        connections_queryset = (
            Connection.objects.filter(
                # 1. Matches where the current entity is either party
                Q(action_by=entity) | Q(involved_entity=entity),
                # 3. Exclude self-referencing connections
                ~Q(action_by=F("involved_entity")),
                # 4/5. Each side must be an active+verified user OR an active
                # realm. The old user-only form returned NOTHING when `entity`
                # was a page (its own side could never match) and dropped every
                # connection whose counterpart was a page.
                entity_side_is_visible("action_by"),
                entity_side_is_visible("involved_entity"),
                # 2. Connection is active
                status=True,
            )
            .order_by("connection_id", "-action_date")
            .distinct("connection_id")
            .values_list("action_by_id", "involved_entity_id")
        )

        result_list = [
            {"action_by_id": ab_id, "involved_user_id": iu_id}
            for ab_id, iu_id in connections_queryset
        ]
        flat_values = [v for d in result_list for v in d.values()]
        unique_values = list(set([v for v in flat_values if v != entity.id]))

        return unique_values

    def get_ranked_connections(self, limit=500):
        entity = self.entity
        connections_queryset = (
            Connection.objects.filter(
                Q(Q(action_by=entity) | Q(involved_entity=entity)),
                ~Q(action_by=F("involved_entity")),
                # Same entity-generic predicate as get_connections above.
                entity_side_is_visible("action_by"),
                entity_side_is_visible("involved_entity"),
                status=True,
            )
            .distinct("connection_id")
            .order_by("connection_id", "-interaction_score", "-last_interaction_at")
            .values_list(
                "action_by_id",
                "involved_entity_id",
                "interaction_score",
                "last_interaction_at",
            )
        )

        sorted_connections = sorted(
            connections_queryset,
            key=lambda x: (x[2], x[3]),
            reverse=True,
        )

        result_list = [
            {"action_by_id": ab_id, "involved_entity_id": iu_id}
            for ab_id, iu_id, *_ in sorted_connections
        ]
        unique_values = []
        seen = set()

        for d in result_list:
            # Check both sides of the connection
            for v in d.values():
                if v != entity.id and v not in seen:
                    unique_values.append(v)
                    seen.add(v)

        return unique_values[:limit]

    def get_mutual_connections(self, friend_id):
        # friend_id IS an entity id, so resolve the Entity directly rather
        # than hopping through Account - a connection's other side can be a
        # page, and Account.objects.get(entity_id=...) raised
        # Account.DoesNotExist for those.
        friend_entity = Entity.objects.filter(id=friend_id).first()
        if friend_entity is None:
            return []

        viewer_connections_list = self.get_connections()

        friend_connections = ConnectionHelpers(friend_entity)
        friend_connections_list = friend_connections.get_connections()

        final_intersection_list = list(
            set(viewer_connections_list) & set(friend_connections_list)
        )

        return final_intersection_list
