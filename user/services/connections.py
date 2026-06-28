from user.serializers import ConnectionSerializer
from ..models import Connection, Account
from django.db.models import Q, F
from ..utils.entity import resolve_user_entity


class ConnectionHelpers:
    def __init__(self, user):
        self.user = user

    def get_connections(self):
        user = self.user
        user_entity = resolve_user_entity(user)
        connections_queryset = (
            Connection.objects.filter(
                Q(Q(action_by=user_entity) | Q(involved_user=user_entity)),
                ~Q(action_by=F("involved_user")),
                Q(action_by__source_type="user.account"),
                Q(involved_user__source_type="user.account"),
                status=True,
            )
            .distinct("connection_id")
            .order_by("connection_id", "-action_date")
            .values_list("action_by__source_id", "involved_user__source_id")
        )

        result_list = [
            {"action_by_id": ab_id, "involved_user_id": iu_id}
            for ab_id, iu_id in connections_queryset
        ]
        flat_values = [v for d in result_list for v in d.values()]
        unique_values = list(set([v for v in flat_values if str(v) != str(user.id)]))

        valid_account_ids = set(
            Account.objects.filter(
                id__in=unique_values, is_active=True, is_verified=True
            ).values_list("id", flat=True)
        )

        return [account_id for account_id in unique_values if account_id in valid_account_ids]

    def get_ranked_connections(self, limit=500):
        user = self.user
        user_entity = resolve_user_entity(user)
        connections_queryset = (
            Connection.objects.filter(
                Q(Q(action_by=user_entity) | Q(involved_user=user_entity)),
                ~Q(action_by=F("involved_user")),
                Q(action_by__source_type="user.account"),
                Q(involved_user__source_type="user.account"),
                status=True,
            )
            .distinct("connection_id")
            .order_by("connection_id", "-interaction_score", "-last_interaction_at")
            .values_list(
                "action_by__source_id",
                "involved_user__source_id",
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
                if str(v) != str(user.id) and v not in seen:
                    unique_values.append(v)
                    seen.add(v)

        valid_account_ids = set(
            Account.objects.filter(
                id__in=unique_values, is_active=True, is_verified=True
            ).values_list("id", flat=True)
        )

        return [
            account_id
            for account_id in unique_values
            if account_id in valid_account_ids
        ][:limit]

    def get_mutual_connections(self, friend_id):
        friend = Account.objects.get(id=friend_id)

        viewer_connections_list = self.get_connections()

        friend_connections = ConnectionHelpers(friend)
        friend_connections_list = friend_connections.get_connections()

        final_intersection_list = list(
            set(viewer_connections_list) & set(friend_connections_list)
        )

        return final_intersection_list
