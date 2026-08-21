"""
Request-scoped failure logging.

Two gaps in the default setup, both of which left production errors invisible:

1. Django's `django.request` logger only fires for exceptions that escape the
   view. Nearly every view here wraps its body in a broad `except Exception`
   and RETURNS a 500 instead, so the exception never reaches Django. Django
   does still log the bare fact of the 5xx ("Internal Server Error: /path"
   from `log_response()` in django/core/handlers/base.py), but that line
   carries no body and no actor.

2. Client errors were invisible too. Django does log 4xx, but at WARNING on
   `django.request` - which the LOGGING config pins to ERROR - so nothing
   recorded them. And this codebase signals application-level failure with
   `{"status": False}` in the body, which occasionally ships with a 200 that
   no status-based rule would catch at all.

This middleware covers both from the outside: it reports the response body -
which for this codebase's `except` blocks is `str(e)`, the only surviving
trace of the exception - plus who sent the request and from where.

It deliberately does NOT log a traceback. For genuinely unhandled exceptions
Django already logs one via `django.request` and duplicating it would print
every stack twice; for swallowed ones there is no traceback left to recover
by the time the response reaches here, which is why the `except` blocks call
`logger.exception(...)` themselves.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Body keys whose values must never reach the log stream.
_SENSITIVE_KEYS = {
    "token",
    "usertoken",
    "authtoken",
    "password",
    "raw_password",
    "new_password",
    "old_password",
    "secret",
}

# A failure body is normally `{"status": False, "message": "<str(e)>"}` or a
# bare string. Anything longer is a rendered page or a serializer dump, not an
# error message, and is truncated rather than dumped whole.
_MAX_BODY_CHARS = 2000


def _describe_actor(request):
    """Best-effort user/entity identification, safe on anonymous requests."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return "anonymous"

    # AutheticationBackend mirrors `entity` onto the underlying HttpRequest
    # (DRF only does that for `user`), so this resolves for authenticated
    # API calls and degrades to the username elsewhere.
    entity = getattr(request, "entity", None)
    entity_id = getattr(entity, "id", None)
    username = getattr(user, "username", "?")
    if entity_id:
        return f"{username} (entity={entity_id})"
    return str(username)


def _redact(data):
    if isinstance(data, dict):
        return {
            key: ("<redacted>" if str(key).lower() in _SENSITIVE_KEYS else value)
            for key, value in data.items()
        }
    return data


def _describe_body(response):
    """Pull the error message out of a DRF Response without leaking secrets."""
    data = getattr(response, "data", None)
    if data is None:
        return "<no data>"

    text = str(_redact(data))
    if len(text) > _MAX_BODY_CHARS:
        return text[:_MAX_BODY_CHARS] + "... <truncated>"
    return text


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        # Left-most entry is the originating client; the rest are proxies.
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _is_declared_failure(response):
    """True for this codebase's `{"status": False, ...}` failure convention."""
    data = getattr(response, "data", None)
    return isinstance(data, dict) and data.get("status") is False


class ApiFailureLogMiddleware:
    """
    Logs every failed response: 5xx at ERROR, 4xx at WARNING, plus any 2xx
    whose body declares `{"status": False}`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        status_code = response.status_code

        if status_code >= 500:
            # A server fault. Always logged - never gated behind the flag
            # below, since these are the ones that must never go missing.
            level = logging.ERROR
        elif status_code >= 400 or _is_declared_failure(response):
            # Any 4xx, plus the handful of responses that return 200 while
            # declaring `{"status": False}` in the body. WARNING keeps a
            # rejected request distinguishable from a server fault at a
            # glance, and lets both be filtered separately by level.
            if not getattr(settings, "LOG_API_FAILURES", True):
                return response
            level = logging.WARNING
        else:
            return response

        logger.log(
            level,
            "%s %s -> %s | actor=%s | ip=%s | body=%s",
            request.method,
            request.get_full_path(),
            response.status_code,
            _describe_actor(request),
            _client_ip(request),
            _describe_body(response),
        )

        return response
