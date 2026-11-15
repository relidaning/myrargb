"""Generate an offline chart report from UTC training logs; no model loading."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from model.experiment_metrics import load_events, record_event, render


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, choices=range(1, 32), default=7)
    parser.add_argument('--run', help='Only include this run identifier')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/training/report.html')
    args = parser.parse_args()
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc)
    paths = [ROOT / 'logs' / f'{today-timedelta(days=i):%Y-%m-%d}.log' for i in reversed(range(args.days))]
    events, malformed, limited = load_events([p for p in paths if p.exists()])
    if args.run:
        events = [e for e in events if e['run'] == args.run]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(events, malformed, limited), encoding='utf-8')
    record_event('report', 'report_generated', {'runs': len({e['run'] for e in events})})
    print(args.output)


if __name__ == '__main__':
    main()
