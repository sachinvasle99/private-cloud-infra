FROM python:3.12-slim

LABEL maintainer="RKE2 Deep Monitor"
LABEL description="Continuous cluster monitoring (Prometheus-based) with time-series metrics, live dashboard, and remediation suggestions"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY service ./service

RUN mkdir -p /app/data \
    && groupadd -r monitor && useradd -r -g monitor -d /app monitor \
    && chown -R monitor:monitor /app
USER monitor

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8080/api/health || exit 1

ENV PYTHONUNBUFFERED=1 \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=8080 \
    RETENTION_DAYS=7 \
    LOG_LEVEL=INFO \
    DB_PATH=/app/data/metrics.db

CMD ["python", "-m", "service.app"]
