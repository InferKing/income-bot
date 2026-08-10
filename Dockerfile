FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY migrations /app/migrations
COPY scripts /app/scripts
COPY OPERATIONS.md /app/OPERATIONS.md
COPY alembic.ini /app/alembic.ini
COPY docker/entrypoint.sh /app/docker/entrypoint.sh

RUN pip install --upgrade pip && pip install . && chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /var/lib/income-tg/models /var/backups/income-tg /run/income-tg \
    && chown -R app:app /var/lib/income-tg /var/backups/income-tg /run/income-tg

USER app

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["income-tg-bot"]
