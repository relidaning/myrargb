"""Evaluate fresh T5 or train an isolated trial. Never resumes existing models."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from model.experiment_runner import load_config, run


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['baseline','train'])
    parser.add_argument('--config', type=Path, default=ROOT/'config/experiment.toml')
    args = parser.parse_args()
    run(load_config(args.config), args.mode)
