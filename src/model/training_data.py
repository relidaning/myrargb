"""Training examples and stable title-grouped validation, without ML imports."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import random
import tempfile

from utils.media import effective_metadata, strip_episode_tags
from utils.titles import title_key


def clean_target(title):
    return ' '.join((title or '').replace('.', ' ').split())


def title_accuracy(predictions, references):
    if not references or len(predictions) != len(references):
        raise ValueError('Generated predictions and references must have equal nonzero lengths')
    size = len(references)
    return {
        'exact_match': sum(' '.join(a.split()) == ' '.join(b.split())
                           for a, b in zip(predictions, references)) / size,
        'title_match': sum(title_key(a) == title_key(b)
                           for a, b in zip(predictions, references)) / size,
    }


@dataclass
class TrainingSplit:
    train: list
    validation: list
    skipped_ids: list
    manifest: dict
    previously_trained_validation_ids: list

    def summary(self):
        return {
            'training_examples': len(self.train),
            'validation_examples': len(self.validation),
            'training_ids': [row['id'] for row in self.train],
            'validation_ids': [row['id'] for row in self.validation],
            'skipped_ids': self.skipped_ids,
            'previously_trained_validation_ids': self.previously_trained_validation_ids,
        }


def prepare_split(items, seed, validation_fraction, manifest=None):
    groups = {}
    skipped = []
    previously_trained = set()
    for item in sorted(items, key=lambda row: row.id or 0):
        target = clean_target(item.title_accurate)
        if not target or not item.filename or not item.filename.strip():
            skipped.append(item.id)
            continue
        group_title = strip_episode_tags(target) if effective_metadata(item)['media_type'] == 'tv' else target
        key = title_key(group_title)
        if not key:
            skipped.append(item.id)
            continue
        groups.setdefault(key, []).append({
            'id': item.id,
            'noisy': f'clean the title: {item.filename}',
            'clean': target,
        })
        if item.trained_flag == '1':
            previously_trained.add(item.id)
    if len(groups) < 2:
        raise ValueError('Training requires at least two distinct corrected title groups')

    if manifest is None:
        keys = sorted(groups)
        random.Random(seed).shuffle(keys)
        count = min(len(keys) - 1, max(1, round(len(keys) * validation_fraction)))
        manifest = {
            'version': 1, 'seed': seed, 'validation_fraction': validation_fraction,
            'validation_groups': sorted(keys[:count]),
        }
    elif (manifest.get('version') != 1 or manifest.get('seed') != seed
          or manifest.get('validation_fraction') != validation_fraction
          or not isinstance(manifest.get('validation_groups'), list)
          or not all(isinstance(key, str) for key in manifest['validation_groups'])):
        raise ValueError('Training split manifest does not match training_seed/training_validation_fraction; '
                         'choose a new training_split_path to deliberately create a new split')
    validation_keys = set(manifest['validation_groups'])
    train = [row for key in sorted(groups) if key not in validation_keys for row in groups[key]]
    validation = [row for key in sorted(groups) if key in validation_keys for row in groups[key]]
    if not train or not validation:
        raise ValueError('The saved training split needs both training and validation examples')
    return TrainingSplit(train, validation, skipped, manifest,
                         sorted(row['id'] for row in validation if row['id'] in previously_trained))


def read_manifest(path):
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text())
        if not isinstance(manifest, dict):
            raise ValueError()
        return manifest
    except (ValueError, OSError):
        raise ValueError('Cannot read training_split_path; restore its manifest or select a new path') from None


def write_json(path, value):
    """Atomic replacement prevents partial manifests/reports after interruption."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(value, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
