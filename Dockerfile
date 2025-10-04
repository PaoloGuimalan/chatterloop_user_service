# Use official Python base image with specific version
FROM python:3.13-slim

# Set working directory inside the container
WORKDIR /app

# Prevent Python from writing .pyc files & buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies and cleanup in one RUN (keep image small)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first to leverage Docker cache for dependencies
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project files to container
COPY . /app/

# Expose port 8000 for Django
EXPOSE 8000

# Default command to run Django development server (change for production)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
