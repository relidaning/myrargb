"""Immutable, grouped experiment partitions; no database or ML imports."""
import hashlib
import json
from pathlib import Path
import random

from model.training_data import clean_target, write_json
from utils.media import strip_episode_tags
from utils.titles import title_key


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source, output, seed, validation_fraction, test_fraction, sizes):
    if not (0 < validation_fraction < 1 and 0 < test_fraction < 1
            and validation_fraction + test_fraction < 1):
        raise ValueError('validation_fraction and test_fraction must be positive with sum < 1')
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError('split_seed must be a nonnegative integer')
    if not sizes or any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in sizes):
        raise ValueError('training_sizes must contain positive integers')
    if output.exists():
        raise ValueError('output already exists; reuse its frozen manifest or select a new output')
    raw = source.read_text(encoding='utf-8')
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not isinstance(rows, list):
        raise ValueError('source must be a JSON array or JSONL records')
    groups, seen = {}, {}
    duplicates = 0
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or not isinstance(row.get('input'), str)
                or not isinstance(row.get('target'), str)):
            raise ValueError(f'Invalid input/target types at source row {index + 1}')
        name, target = row['input'].strip(), clean_target(row['target'])
        group = title_key(strip_episode_tags(target))
        if not name or not target or not group:
            raise ValueError(f'Empty input/target/title group at source row {index + 1}')
        input_key = ' '.join(name.casefold().split())
        if input_key in seen:
            if seen[input_key] != target:
                raise ValueError(f'Conflicting labels at source row {index + 1}; resolve before splitting')
            duplicates += 1
            continue
        seen[input_key] = target
        groups.setdefault(group, []).append(dict(id=row.get('id', index + 1), input=name, target=target))
    keys = sorted(groups)
    if len(keys) < 3:
        raise ValueError('At least three distinct title groups are required')
    rng = random.Random(seed)
    rng.shuffle(keys)
    nv, nt = max(1, round(len(keys)*validation_fraction)), max(1, round(len(keys)*test_fraction))
    if nv + nt >= len(keys):
        raise ValueError('Holdout fractions leave no training groups')
    assignments = dict(validation=keys[:nv], test=keys[nv:nv+nt], train=keys[nv+nt:])
    partitions = {name: [row for key in members for row in groups[key]]
                  for name, members in assignments.items()}
    # One seeded order gives exact-size nested samples. Held-out groups stay excluded.
    rng.shuffle(partitions['train'])
    available = len(partitions['train'])
    actual_sizes = sorted(set([n for n in sizes if n <= available] + [available]))
    manifest = dict(version=1, source_sha256=digest(source), split_seed=seed,
                    requested_training_sizes=sizes, available_training_examples=available,
                    training_sizes=actual_sizes, source_rows=len(rows),
                    duplicate_inputs_removed=duplicates, unique_title_groups=len(groups),
                    validation_fraction=validation_fraction, test_fraction=test_fraction,
                    fraction_unit='title_groups', groups=assignments,
                    counts={k:len(v) for k,v in partitions.items()}, files={})
    output.mkdir(parents=True)
    def save(name, records):
        path = output / name
        path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records), encoding='utf-8')
        manifest['files'][name] = digest(path)
    for name, records in partitions.items():
        save(name+'.jsonl', records)
    for n in actual_sizes:
        save(f'train_{n}.jsonl', partitions['train'][:n])
    write_json(output/'manifest.json', manifest)
    return manifest
