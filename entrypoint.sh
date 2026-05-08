#!/bin/bash
set -e

ls -la /app

# Then start the app
exec "$@"
