#!/bin/sh
set -e
case "$1" in
  web)
    python -m app.migrate
    exec uvicorn app.web:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
    ;;
  scheduler)
    exec python -m app.scheduler
    ;;
  *)
    exec "$@"
    ;;
esac
