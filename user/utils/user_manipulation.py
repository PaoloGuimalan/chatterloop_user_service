from ..models import Account
from datetime import datetime
from django.utils.timezone import make_aware
from django.utils.timezone import now
from ..utils.bcrypt_tools import hash_password
from ..utils.generators import generate_unique_username
from ..models import UserEngagementLog
from django.utils.timezone import now, is_naive, make_aware, get_current_timezone
from django.utils.dateparse import parse_datetime
import uuid


def create_user(
    first_name,
    middle_name,
    last_name,
    email,
    raw_password,
    birthday,
    birthmonth,
    birthyear,
    gender,
    join_type,
):
    try:
        if not middle_name or middle_name.strip() == "":
            middle_name = "N/A"
        else:
            middle_name = middle_name.strip()

        if Account.objects.filter(email=email).exists():
            print("Email already in use")
            raise ValueError("Email already in use")

        username = generate_unique_username(first_name)

        birthdate = None

        if birthday is not None and birthmonth is not None and birthyear is not None:
            birthdate_naive = datetime(birthyear, birthmonth, birthday)
            birthdate = make_aware(birthdate_naive)

        hashed_password = hash_password(raw_password)

        new_user = Account(
            username=username,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            email=email,
            password=hashed_password,
            birthdate=birthdate,
            gender=gender,
            profile="none",
            date_created=now(),
            is_active=True,
            is_verified=True,
            is_default_user=True,
            is_superuser=False,
            join_type=join_type,
        )
        new_user.save()

        return new_user
    except Exception as ex:
        print(str(ex))
        raise ValueError(str(ex))


def save_profile_visit(user, profile_id, target_type):
    try:
        user_id = uuid.UUID(user.id) if isinstance(user.id, str) else user.id

        if str(profile_id) == str(user.id):
            return

        log = UserEngagementLog(
            user_id=user_id,
            activity_time=now(),
            time_spent=float(0),
            activity_type="visit",
            target_type=target_type,
            target_id=str(profile_id),
        )
        log.save()
        return log

    except Exception as ex:
        print(ex)
        return None
