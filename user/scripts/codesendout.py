from ..utils.external_requests import emailer
from ..models import Account


def run():

    unverified_users = Account.objects.filter(is_verified=False).prefetch_related(
        "verification_set"
    )

    for account in unverified_users:
        # This won't hit the DB again because of prefetch_related
        verifications = account.verification_set.all()

        # Example: Get the most recent code
        latest = verifications.filter(is_used=False).order_by("-date_generated").first()

        if latest:
            emailer.send_email_verification_code(
                to_email=account.email,
                subject="Verification Code",
                user_id=account.username,
                body=f"""
                Welcome to ChatterLoop!

                Your registration was successful! Here is your verification code for the account activation: {latest.ver_code}
                """,
            )
            print(
                f"User: {account.email} | Code: {latest.ver_code} | Is Used: {latest.is_used}"
            )
