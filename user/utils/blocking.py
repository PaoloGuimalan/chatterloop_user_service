# Moved to entity/services/blocking.py with the Block model. Re-exported here
# so the eight existing `from user.utils.blocking import ...` call sites keep
# resolving; new code should import from entity.services.blocking directly.

from entity.services.blocking import (  # noqa: F401
    get_blocked_account_ids,
    is_blocked,
)
