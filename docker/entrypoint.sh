#!/bin/sh
set -eu

if [ "${INCOME_TG_SKIP_BOOTSTRAP:-false}" != "true" ]; then
    alembic upgrade head
    income-tg-bootstrap
fi
exec "$@"
