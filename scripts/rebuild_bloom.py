#!/usr/bin/env python3
"""Back up and rebuild the title hint index. Stop the app before running."""

from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bloom_filter import BloomFilter
from utils.settings import settings
from utils.titles import title_key


def main():
    with sqlite3.connect(settings.database_path.as_uri() + '?mode=ro', uri=True) as connection:
        keys = {title_key(title) for row in connection.execute('SELECT title, title_accurate FROM items')
                for title in row if title_key(title)}
    path = settings.bloom_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory) / 'bloom.bin'
        bloom = BloomFilter(filename=str(temporary))
        for key in keys:
            bloom.add(key)
        if not all(key in bloom for key in keys):
            raise RuntimeError('Rebuilt Bloom filter verification failed')
        if path.exists():
            backups = settings.database_path.parent / 'backups'
            backups.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = backups / f'bloom-{stamp}.bin'
            shutil.copy2(path, backup)
            print(f'Previous index: {backup}')
        os.replace(temporary, path)
    print(f'Rebuilt and verified {len(keys)} normalized title keys; catalog unchanged.')


if __name__ == '__main__':
    main()
