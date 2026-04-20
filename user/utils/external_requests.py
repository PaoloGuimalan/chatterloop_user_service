from ..models import Account, Verification
from django.utils.timezone import now
from .generators import make_id
from user_service.settings import MAILINGSERVICE
from django.core import mail
from django.conf import settings
import requests


def send_email_verification_code(from_email, to_email, subject, user_id, body=None):
    generated_code = make_id(6)  # your random code generator for your logic

    content = (
        body
        if body
        else f"""
    Welcome to ChatterLoop!

    Your registration was successful! Here is your verification code for the account activation: {generated_code}
    """
    )

    connection = mail.get_connection(
        username=settings.EMAIL_VERIFY_USER, password=settings.EMAIL_VERIFY_PASS
    )
    connection.open()

    try:
        mail.send_mail(
            subject,
            content,
            settings.EMAIL_VERIFY_USER,
            [to_email],
            connection=connection,
        )
        # response = requests.post(f"{MAILINGSERVICE}/sendEmail", json=payload)
        # if response.status_code == 200 and response.json().get("status") == True:
        # Save your Verification record here only if email sent
        ver_record = Verification(
            user=Account.objects.get(username=user_id),
            ver_code=generated_code,
            date_generated=now(),
            is_used=False,
        )
        ver_record.save()
        return True
    # else:
    #     print("Failed to send email:", response.text)
    #     return False
    except Exception as e:
        print("Error sending verification email:", e)
        return False
