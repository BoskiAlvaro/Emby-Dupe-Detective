#!/bin/sh
mkdir -p /app/data
chown -R appuser:appuser /app/data
PORT="${APP_PORT:-5000}"
exec su -s /bin/sh -c "exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 2 --timeout 120 app:app" appuser