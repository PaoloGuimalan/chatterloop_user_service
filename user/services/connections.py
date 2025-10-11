from user.serializers import ConnectionSerializer
from ..models import Connection
from django.db.models import Q, F


class ConnectionHelpers:
    def __init__(self, user):
        self.user = user

    def get_connections(self):
        user = self.user
        connections_queryset = (
            Connection.objects.filter(
                Q(Q(action_by=user) | Q(involved_user=user)),
                ~Q(action_by=F("involved_user")),
                Q(action_by__is_active=True),
                Q(action_by__is_verified=True),
            )
            .distinct("connection_id")
            .order_by("connection_id", "-action_date")
            .values_list("action_by_id", "involved_user_id")
        )

        result_list = [
            {"action_by_id": ab_id, "involved_user_id": iu_id}
            for ab_id, iu_id in connections_queryset
        ]
        flat_values = [v for d in result_list for v in d.values()]
        unique_values = list(set([v for v in flat_values if v != user.id]))

        return unique_values
