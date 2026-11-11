"""Stable launch entrypoint for the application in src/."""

from pathlib import Path
import sys

project_root = Path(__file__).resolve().parent
# Support both direct launch and loading this file from another working directory.
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'src'))

from src.app import app, main


if __name__ == '__main__':
    main()
