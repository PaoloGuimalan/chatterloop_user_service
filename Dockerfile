# --- Stage 1: Build Stage ---
FROM python:3.13-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Build dependencies into wheels to save space in final image
COPY requirements.txt .

# django-cassandra-engine 1.10.0 publishes no wheel, and its pyproject asks for
# an UNPINNED build backend ("poetry>=1.1.12"). poetry-core 2.x converts its
# django constraint into invalid PEP 508 metadata - `django (>=4.2,<5.0 ||
# >=5.0,<6.0)` - which pip >= 24.1 refuses. This build therefore broke without
# anything in this repo changing: the backend moved, not us.
#
# Building it here against a pinned backend produces correct metadata - the same
# `django (>=4.2,<6.0)` the sdist itself declares. --no-build-isolation is what
# makes pip use THIS poetry instead of resolving a fresh one. PIP_CONSTRAINT is
# not a substitute: pip does not reliably apply it to build dependencies.
RUN pip install --no-cache-dir "poetry<2" \
    && pip wheel --no-cache-dir --no-deps --no-build-isolation \
    --wheel-dir /app/wheels django-cassandra-engine==1.10.0

# --find-links makes this reuse the wheel built above rather than going back to
# the sdist and regenerating the broken metadata.
RUN pip wheel --no-cache-dir --no-deps --find-links /app/wheels \
    --wheel-dir /app/wheels -r requirements.txt


# --- Stage 2: Final Runtime Stage ---
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8003

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/wheels /wheels
COPY requirements.txt .

RUN pip install --no-cache-dir \
      --find-links /wheels \
      -r requirements.txt

COPY . .

RUN useradd -m django_user && chown -R django_user /app
USER django_user

EXPOSE 8003

CMD ["sh", "-c", "python manage.py collectstatic --noinput && exec gunicorn --bind 0.0.0.0:8003 --workers 3 --timeout 120 --access-logfile - --error-logfile - user_service.wsgi:application"]