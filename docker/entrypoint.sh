#!/bin/bash
set -e

# Source code lives under /app/src; app.py adds it to the import path.
exec "$@"
