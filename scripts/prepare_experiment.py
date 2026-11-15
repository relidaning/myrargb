"""Audit source examples and freeze independent title-grouped experiment splits."""
import argparse
from pathlib import Path
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from model.experiment_data import prepare
from model.experiment_metrics import record_event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'config/experiment.toml')
    args = parser.parse_args()
    started = time.monotonic()
    try:
        config = tomllib.loads(args.config.read_text())
        manifest = prepare(ROOT/config['source'], ROOT/config['output'], config['split_seed'],
                           config['validation_fraction'], config['test_fraction'], config['training_sizes'])
        record_event('prepare', 'data_prepared', {**manifest['counts'],
                     'duration_seconds': time.monotonic()-started})
        print('Frozen partitions:', manifest['counts'])
        print('Training sizes:', manifest['training_sizes'])
        print('Duplicate inputs removed:', manifest['duplicate_inputs_removed'])
        print('Manifest:', ROOT/config['output']/'manifest.json')
    except Exception:
        record_event('prepare', 'data_preparation_failed', {'duration_seconds':time.monotonic()-started})
        raise


if __name__ == '__main__':
    main()
