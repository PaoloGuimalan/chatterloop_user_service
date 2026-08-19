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
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt


# --- Stage 2: Final Runtime Stage ---
FROM python:3.13-slim

# Set recommended production environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8003

WORKDIR /app

# Install only essential runtime system libraries
RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder and install them
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache /wheels/*

# Copy the rest of your application code
COPY . .

# Security: Create and use a non-root user
RUN useradd -m django_user && chown -R django_user /app
USER django_user

# Expose the production port
EXPOSE 8003

# Run with Gunicorn instead of manage.py runserver
# Workers formula: (2 * cores) + 1
# collectstatic runs here, not at build time: django_cassandra_engine opens a
# real Cassandra connection during django.setup(), so manage.py needs the
# runtime secret. `exec` keeps gunicorn as PID 1 so SIGTERM still reaches it.
CMD ["sh", "-c", "python manage.py collectstatic --noinput && exec gunicorn --bind 0.0.0.0:8003 --workers 3 --timeout 120 user_service.wsgi:application"]
