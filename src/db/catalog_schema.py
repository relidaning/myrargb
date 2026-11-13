"""Migrate the mixed catalog to items and add release metadata."""

from collections import Counter
from utils.media import release_metadata

COLUMNS = {
    'media_type': "TEXT DEFAULT 'unknown'",
    'release_scope': "TEXT DEFAULT 'unknown'",
    'season': 'INTEGER', 'season_end': 'INTEGER',
    'episode': 'INTEGER', 'episode_end': 'INTEGER',
}


def migrate_catalog(connection, refresh=False):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'movies' in tables and 'items' not in tables:
        connection.execute('ALTER TABLE movies RENAME TO items')
    existing = {row[1] for row in connection.execute('PRAGMA table_info(items)')}
    added = []
    for column, definition in COLUMNS.items():
        if column not in existing:
            connection.execute(f'ALTER TABLE items ADD COLUMN {column} {definition}')
            added.append(column)
    counts = Counter()
    if added or refresh:
        columns = ', '.join(COLUMNS)
        for row in connection.execute(f'SELECT id, filename, {columns} FROM items').fetchall():
            ident, filename = row[:2]
            existing_metadata = dict(zip(COLUMNS, row[2:]))
            metadata = release_metadata(filename)
            if metadata['media_type'] == 'unknown' and existing_metadata['media_type'] in ('movie', 'tv'):
                # A source category can identify releases without filename markers.
                metadata = existing_metadata
            counts[metadata['media_type']] += 1
            if metadata == existing_metadata:
                continue
            assignments = ', '.join(f'{key} = ?' for key in metadata)
            connection.execute(f'UPDATE items SET {assignments} WHERE id = ?',
                               (*metadata.values(), ident))
    connection.execute('CREATE INDEX IF NOT EXISTS idx_items_media_type ON items(media_type)')
    return {'added_columns': added, 'classified': dict(counts)}
