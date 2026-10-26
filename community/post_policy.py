"""Database-independent rules shared by community readers and contributors."""


def can_read(*, is_private, is_member, is_active=True):
    return bool(is_active and (not is_private or is_member))


def can_contribute(*, is_member, is_active=True):
    return bool(is_active and is_member)


def can_interact(*, is_private, is_member, is_active=True):
    """Public readers may comment/react; publishing still requires membership."""
    return bool(is_active and (not is_private or is_member))


def validate_anonymity(value, *, is_community):
    if type(value) is not bool:
        raise ValueError("is_anonymous must be true or false")
    if value and not is_community:
        raise ValueError("Anonymous contributions require a community")
    return value


def project_contribution(data, *, is_anonymous, owner_id, viewer_id):
    result = dict(data)
    result["is_anonymous"] = bool(is_anonymous)
    result["is_owner"] = viewer_id is not None and str(owner_id) == str(viewer_id)
    if is_anonymous:
        result["entity"] = None
        for key in ("deleted_by", "feed_reason", "author"):
            if key in result:
                result[key] = None
    return result
