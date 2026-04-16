#!/usr/bin/env bash
set -o errexit

gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000}
