FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/sensors.db \
    PROMETHEUS_DISABLE_CREATED_SERIES=True

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root user; /data is pre-created so a fresh named volume inherits this ownership.
RUN useradd --system --uid 10001 app && mkdir -p /data && chown app:app /data

COPY app/ app/
COPY simulator/ simulator/

USER app
EXPOSE 8000
CMD ["python", "-m", "app"]
