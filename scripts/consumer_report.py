"""Generate a bounded local HTML report from completed Kafka jobs in daily logs."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from utils.settings import settings


def render(paths, group, start, end, max_bytes=64 * 1024 * 1024):
    buckets = defaultdict(list)
    malformed = scanned = 0
    limited = False
    freshest = None
    for path in paths:
        if not path.exists():
            continue
        with path.open('rb') as source:
            for raw in source:
                scanned += len(raw)
                if scanned > max_bytes:
                    limited = True
                    break
                if b'job_completed ' not in raw:
                    continue
                try:
                    event = json.loads(raw.split(b'job_completed ', 1)[1])
                    stamp = datetime.fromisoformat(event['timestamp'])
                    duration = float(event['duration_seconds'])
                    if not math.isfinite(duration) or duration < 0 or event['outcome'] not in ('success', 'failure'):
                        raise ValueError('Invalid event')
                    if event['group'] != group or not start <= stamp < end:
                        continue
                    hour = stamp.replace(minute=0, second=0, microsecond=0)
                    buckets[hour].append((event['outcome'], duration))
                    freshest = max(freshest or stamp, stamp)
                except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                    malformed += 1
        if limited:
            break
    rows = []
    for hour, values in sorted(buckets.items()):
        count = len(values)
        failures = sum(outcome == 'failure' for outcome, _ in values)
        duration = sum(seconds for _, seconds in values) / count
        seconds = (min(hour + timedelta(hours=1), end) - max(hour, start)).total_seconds()
        rows.append(f'<tr><td>{hour:%Y-%m-%d %H:00}</td><td>{count}</td>'
                    f'<td>{failures / count:.1%}</td><td>{duration:.2f}</td>'
                    f'<td>{count / seconds:.4f}</td><td><meter min="0" max="{max(len(v) for v in buckets.values())}" value="{count}">{count}</meter></td></tr>')
    return (f'<!doctype html><meta charset="utf-8"><title>Consumer throughput</title>'
            f'<style>body{{font:16px system-ui;margin:2rem}}td,th{{padding:.5rem;text-align:left}}meter{{width:240px}}</style>'
            f'<h1>Consumer throughput</h1><p>{escape(group)}</p>'
            f'<p>UTC range: {start.isoformat()} to {end.isoformat()}. Latest event: {freshest or "unavailable"}.</p>'
            f'<p>Malformed events: {malformed}. Scan limit reached: {limited}. Missing files may indicate retention gaps; empty hours have no observations.</p>'
            '<p>Each completed callback counts once, including failed callbacks. Browser retries stay within that callback. '
            'Kafka redeliveries count as separate attempts. This measures processing attempts, not unique movies or broker lag.</p>'
            '<table><tr><th>Hour (UTC)</th><th>Completed</th><th>Failure rate</th><th>Mean duration (s)</th><th>Jobs/s</th><th>Volume</th></tr>'
            + ''.join(rows) + '</table>' + ('<p>No completed jobs recorded.</p>' if not rows else ''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=1, choices=range(1, 32))
    parser.add_argument('--group', default='xyz.lidaning.myrargb.consumers.crawl_imdb')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    end = datetime.now(timezone.utc)
    start = (end - timedelta(days=args.days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    paths = list(dict.fromkeys(Path(day.strftime(str(settings.log_path))) for day in
                              (start + timedelta(days=i) for i in range(args.days))))
    args.output.write_text(render(paths, args.group, start, end), encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
