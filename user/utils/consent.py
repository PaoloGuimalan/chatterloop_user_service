from django.utils.timezone import now
from core.models import PolicyDocument
from ..models import UserConsent


def get_current_policy_documents():
    """Latest effective PolicyDocument per document_type, keyed by document_type."""
    current = {}
    for doc in PolicyDocument.objects.filter(effective_date__lte=now()).order_by(
        "document_type", "-effective_date"
    ):
        if doc.document_type not in current:
            current[doc.document_type] = doc
    return current


def get_pending_consents(account):
    current_docs = get_current_policy_documents()
    if not current_docs:
        return []

    accepted = set(
        UserConsent.objects.filter(
            user=account, document_type__in=current_docs.keys()
        ).values_list("document_type", "version")
    )

    pending = []
    for document_type, doc in current_docs.items():
        if (document_type, doc.version) not in accepted:
            pending.append({"document_type": document_type, "version": doc.version})
    return pending


def record_consent_acceptance(account, document_types, ip_address=None, user_agent=None):
    current_docs = get_current_policy_documents()
    created = []
    for document_type in document_types:
        doc = current_docs.get(document_type)
        if not doc:
            continue
        already_accepted = UserConsent.objects.filter(
            user=account, document_type=document_type, version=doc.version
        ).exists()
        if already_accepted:
            continue
        consent = UserConsent.objects.create(
            user=account,
            document_type=document_type,
            version=doc.version,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        created.append(consent)
    return created
