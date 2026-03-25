# syntax = docker/dockerfile:1.4.0

FROM python:3.13-alpine AS build
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install uv (fast pip replacement, same as Uber)
ADD https://astral.sh/uv/install.sh /tmp/install-uv.sh
RUN sh /tmp/install-uv.sh && rm /tmp/install-uv.sh
ENV PATH=/root/.local/bin:$PATH

# Install system dependencies for psycopg2
RUN --mount=type=cache,target=/var/cache/apk \
    apk --update-cache upgrade && \
    apk add postgresql-dev gcc musl-dev

# Install Python dependencies
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

# --- Test stage (optional, for future CI use) ---
FROM build AS test
COPY requirements-dev.txt .
RUN uv pip install --system -r requirements-dev.txt
COPY . .
CMD ["python3", "-m", "pytest"]

# --- Release stage ---
FROM build AS release
COPY . .

# Create non-root user
RUN adduser -D appuser
USER appuser

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "4", "app.wsgi:app"]
