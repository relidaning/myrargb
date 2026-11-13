#!/usr/bin/env python3
"""Back up the catalog and add/backfill movie and TV release metadata."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from db.catalog_schema import migrate_catalog
from utils.settings import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='Reparse existing release metadata after parser updates')
    args = parser.parse_args()
    directory = settings.database_path.parent / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = directory / f'catalog-types-{stamp}.db'
    with sqlite3.connect(settings.database_path.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
        source.backup(target)
        if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup verification failed')
    with sqlite3.connect(settings.database_path, timeout=10) as connection:
        connection.execute('BEGIN IMMEDIATE')
        result = migrate_catalog(connection, refresh=args.refresh)
        connection.commit()
        assert connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    result['backup'] = str(backup)
    report = backup.with_suffix('.json')
    report.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
