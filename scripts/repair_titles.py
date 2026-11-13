#!/usr/bin/env python3
"""Propose filename-based title repairs; apply only an explicitly reviewed plan."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from utils.settings import settings

TECH = re.compile(r'\b(?:\d{3,4}p|4k|uhd|blu[ ._-]?ray|bdremux|bdrip|brrip|web[ ._-]?(?:dl|rip)|hdtv|dvdrip|hevc|[xh][ .]?26[45]|av1|remux|10bits?|ds4k)\b', re.I)
SEASON = re.compile(r'\b(?:s\d{1,2}(?:e\d{1,3})?|seasons?\s+\d{1,2})\b', re.I)
YEAR = re.compile(r'(?<!\d)(?:19|20)\d{2}(?!\d)')
EVENT = re.compile(r'^(?:formula[ .]1|wwe\b|ufc\b|aew\b|tna\b|one championship\b|nba\b|nfl\b|newsnight\b)', re.I)


def normalized(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value or '').casefold()))


def readable(value):
    if 'Ã' in value or 'Â' in value:
        try:
            value = value.encode('latin1').decode('utf8')
        except (UnicodeError, ValueError):
            pass
    value = re.sub(r'[._]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip(' ._-([{')


def source_title(filename):
    if not filename or filename.startswith('['):
        return None
    tech = TECH.search(filename)
    prefix = filename[:tech.start()] if tech else filename
    if EVENT.search(filename):
        # A sporting event's date/race is part of its identity.
        return readable(prefix)
    seasons = list(SEASON.finditer(prefix))
    if len(seasons) > 1 and re.search(r'[A-Za-z]{3,}', prefix[seasons[0].end():seasons[1].start()]):
        return None  # A filename describing multiple series needs manual review.
    if seasons:
        prefix = prefix[:seasons[0].start()]
    years = [match for match in YEAR.finditer(prefix) if match.start() > 0]
    if years:
        # Last year before release tags protects titles like Blade Runner 2049
        # (2017) and 2001 A Space Odyssey (1968).
        prefix = prefix[:years[-1].start()]
    candidate = readable(prefix)
    if not candidate or len(candidate) > 180 or any(c in candidate for c in '[]()'):
        return None
    return candidate


def model_outputs(log_path):
    outputs = {}
    if not log_path.exists():
        return outputs
    for line in log_path.read_text(errors='replace').splitlines():
        if '# Original: ' in line and ' --> Predicted: ' in line:
            original, result = line.split('# Original: ', 1)[1].split(' --> Predicted: ', 1)
            outputs[original] = result.strip()
    return outputs


def propose(connection, outputs):
    connection.row_factory = sqlite3.Row
    changes = []
    for row in connection.execute(f'SELECT * FROM {catalog_table(connection)} ORDER BY id'):
        original = dict(row)
        if (row['title_accurate'] or '').strip() or not row['title']:
            continue
        candidate = source_title(row['filename'])
        if not candidate or normalized(candidate) == normalized(row['title']):
            continue
        reason = []
        if TECH.search(row['title']):
            reason.append('release-format tags remain in title')
        if SEASON.search(row['title']) and not EVENT.search(row['filename']):
            reason.append('season/episode tags remain in title')
        # Existing scored titles can be IMDb's canonical/localized titles. Only
        # propose semantic repairs when logs prove this is still the raw model output.
        model_result = outputs.get(row['filename'])
        if model_result and normalized(model_result) == normalized(row['title']):
            reason.append('raw model output differs from the filename title')
        if not reason:
            continue
        changes.append({'apply': False, 'id': row['id'], 'before': row['title'],
                        'after': candidate, 'reason': reason, 'original': original})
    return changes


def apply_plan(database, plan, backup_directory, bloom_path, mark_training=False):
    changes = [entry for entry in plan if entry.get('apply')]
    if len({entry['id'] for entry in changes}) != len(changes):
        raise ValueError('Duplicate row IDs in repair plan')
    for entry in changes:
        if (entry['original'].get('title_accurate') or '').strip():
            raise ValueError('Repair plan includes a manually corrected row')
        if not isinstance(entry['after'], str) or not entry['after'].strip():
            raise ValueError('Repair plan includes an empty title')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup = backup_directory / f'title-repair-{stamp}.db'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
        source.backup(target)
        if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup verification failed')
    if bloom_path.exists():
        shutil.copy2(bloom_path, backup.with_suffix('.bloom.bin'))
    applied, skipped = [], []
    with sqlite3.connect(database, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN IMMEDIATE')
        for entry in changes:
            current = connection.execute(f'SELECT * FROM {catalog_table(connection)} WHERE id = ?', (entry['id'],)).fetchone()
            # Do not overwrite concurrent enrichment, manual edits, or deletion.
            if current is None or dict(current) != entry['original']:
                skipped.append(entry['id'])
                continue
            if mark_training:
                connection.execute(f"UPDATE {catalog_table(connection)} SET title = ?, title_accurate = ?, trained_flag = '0' WHERE id = ?",
                                   (entry['after'], entry['after'], entry['id']))
            else:
                connection.execute(f'UPDATE {catalog_table(connection)} SET title = ? WHERE id = ?', (entry['after'], entry['id']))
            applied.append(entry)
        # Add new titles to the existing hint index. Partial additions after a
        # failed transaction are harmless: every duplicate is DB-confirmed.
        if applied:
            from bloom_filter import BloomFilter
            from utils.bloom_utils import normalize
            bloom_path.parent.mkdir(parents=True, exist_ok=True)
            bloom = BloomFilter(filename=str(bloom_path))
            for entry in applied:
                bloom.add(normalize(entry['after']))
        connection.commit()
    report = backup.with_suffix('.json')
    report.write_text(json.dumps({'backup': str(backup), 'applied': applied, 'skipped': skipped, 'marked_for_training': mark_training},
                                 ensure_ascii=False, indent=2))
    return {'applied': len(applied), 'marked_for_training': len(applied) if mark_training else 0, 'skipped': skipped, 'backup': str(backup), 'audit': str(report)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--propose', type=Path, help='Write an unapproved repair plan as JSON')
    group.add_argument('--apply', type=Path, help='Apply entries marked apply=true in a reviewed plan')
    parser.add_argument('--mark-training', action='store_true', help='Save reviewed targets in title_accurate with trained_flag=0')
    args = parser.parse_args()
    if args.propose:
        with sqlite3.connect(settings.database_path.as_uri() + '?mode=ro', uri=True) as connection:
            changes = propose(connection, model_outputs(settings.log_path))
        args.propose.parent.mkdir(parents=True, exist_ok=True)
        args.propose.write_text(json.dumps(changes, ensure_ascii=False, indent=2))
        print(f'Proposed {len(changes)} repairs; none approved or applied.')
    else:
        print(json.dumps(apply_plan(settings.database_path, json.loads(args.apply.read_text()),
                                    settings.database_path.parent / 'backups', settings.bloom_path, args.mark_training)))

def catalog_table(connection):
    return 'items' if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'").fetchone() else 'movies'

if __name__ == '__main__':
    main()
