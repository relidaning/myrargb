#!/bin/sh
# Resolve the same endpoint configuration as the application, from any directory.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${MYRARGB_PYTHON:-"$project_dir/.venv/bin/python"}
app_url=$("$python_bin" - "$project_dir" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]) / 'src'))
from utils.settings import settings
host = settings.host
if host in ('0.0.0.0', '::'):
    host = 'localhost'
if ':' in host:
    host = f'[{host}]'
print(f'http://{host}:{settings.port}')
PY
)
exec curl --fail --show-error --silent "$app_url/crawl_rargb?incremental=true"
