from user.serializers import ConnectionSerializer
from ..models import Connection, Account
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
                # 2. Connection is active
                status=True,
                # 4. Filter action_by's account status (Entity -> Account via 'users')
                action_by__users__is_active=True,
                action_by__users__is_verified=True,
                # 5. Filter involved_entity's account status (Entity -> Account via 'users')
                involved_entity__users__is_active=True,
                involved_entity__users__is_verified=True,
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
                Q(action_by__is_active=True),
                Q(action_by__is_verified=True),
                Q(involved_user__is_active=True),
                Q(involved_user__is_verified=True),
                status=True,
            )
            .distinct("connection_id")
            .order_by("connection_id", "-interaction_score", "-last_interaction_at")
            .values_list(
                "action_by_id",
                "involved_user_id",
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
            {"action_by_id": ab_id, "involved_user_id": iu_id}
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
        friend = Account.objects.get(entity_id=friend_id)

        viewer_connections_list = self.get_connections()

        friend_connections = ConnectionHelpers(friend.entity)
        friend_connections_list = friend_connections.get_connections()

        final_intersection_list = list(
            set(viewer_connections_list) & set(friend_connections_list)
        )

        return final_intersection_list
