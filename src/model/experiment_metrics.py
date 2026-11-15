"""Payload-free training events and dependency-free, offline SVG reports."""
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def record_event(run, phase, metrics, config=None, root=ROOT):
    now = datetime.now(timezone.utc)
    path = root / 'logs' / f'{now:%Y-%m-%d}.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    event = dict(timestamp=now.isoformat(), run=run, phase=phase,
                 metrics=metrics, config=config or {})
    with path.open('a', encoding='utf-8') as output:
        output.write('training_event ' + json.dumps(event, allow_nan=False) + '\n')


def load_events(paths, max_bytes=32 * 1024 * 1024):
    events, malformed, scanned = [], 0, 0
    for path in paths:
        with path.open('rb') as source:
            for raw in source:
                scanned += len(raw)
                if scanned > max_bytes:
                    return events, malformed, True
                if not raw.startswith(b'training_event '):
                    continue
                try:
                    event = json.loads(raw[len(b'training_event '):])
                    datetime.fromisoformat(event['timestamp'])
                    if (not isinstance(event['run'], str) or not isinstance(event['phase'], str)
                            or not isinstance(event['metrics'], dict)
                            or not isinstance(event['config'], dict)):
                        raise ValueError('Invalid event')
                    events.append(event)
                except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                    malformed += 1
    return events, malformed, False


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def chart(title, series):
    points = [p for _, values in series for p in values]
    if not points:
        return f'<h2>{escape(title)}</h2><p>No observations available.</p>'
    xmax = max(1, max(x for x, _ in points))
    ymin = min(0, min(y for _, y in points))
    ymax = max(y for _, y in points)
    span = max(ymax - ymin, .001)
    colors = ['#2563eb', '#b45309', '#047857', '#be123c', '#7c3aed']
    lines, legend = [], []
    for i, (label, values) in enumerate(series):
        color = colors[i % len(colors)]
        coords = ' '.join(f'{65+x/xmax*650:.2f},{230-(y-ymin)/span*195:.2f}' for x,y in values)
        lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{coords}"/>')
        for x,y in values:
            lines.append(f'<circle cx="{65+x/xmax*650:.2f}" cy="{230-(y-ymin)/span*195:.2f}" r="3" fill="{color}"><title>{escape(label)}: epoch {x:g}, value {y:.5g}</title></circle>')
        legend.append(f'<li style="color:{color}">{escape(label)}</li>')
    return (f'<h2>{escape(title)}</h2><svg viewBox="0 0 760 275" role="img" aria-label="{escape(title)}">'
            '<path d="M65 30V230H715" fill="none" stroke="#64748b"/>'
            f'<text x="5" y="40">{ymax:.4g}</text><text x="5" y="230">{ymin:.4g}</text>'
            f'<text x="65" y="250">0</text><text x="680" y="250">{xmax:g}</text>'
            '<text x="350" y="270">Epoch</text>' + ''.join(lines) + '</svg><ul>' + ''.join(legend) + '</ul>')


def render(events, malformed=0, limited=False):
    runs = {}
    for event in events:
        if event['phase'] not in {'started', 'progress', 'baseline', 'final', 'completed', 'failed'}:
            continue
        runs.setdefault(event['run'], []).append(event)
    charts = []
    for title, names in [('Loss (tokens)', ['loss', 'eval_loss']),
                         ('Validation title accuracy (0–1)', ['eval_title_match', 'eval_exact_match'])]:
        series = []
        for run, rows in runs.items():
            for name in names:
                values = [(r['metrics']['epoch'], r['metrics'][name]) for r in rows
                          if r['phase'] == 'progress' and number(r['metrics'].get('epoch'))
                          and number(r['metrics'].get(name))]
                if name.startswith('eval_'):
                    baseline_key = name.replace('eval_', 'baseline_', 1)
                    before = [r['metrics'][baseline_key] for r in rows
                              if r['phase'] == 'baseline' and number(r['metrics'].get(baseline_key))]
                    if before:
                        values.insert(0, (0, before[-1]))
                # A final evaluation after restoring an earlier checkpoint uses
                # the last epoch number too. Keep the actual epoch observation;
                # the restored checkpoint's result belongs in the summary table.
                by_epoch = {}
                for epoch, value in values:
                    by_epoch.setdefault(epoch, value)
                values = list(by_epoch.items())
                if values:
                    series.append((f'{run}: {name}', sorted(values)))
        charts.append(chart(title, series))
    rows = []
    for run, entries in runs.items():
        config = next((r['config'] for r in entries if r['config']), {})
        def value(phase, key):
            found = [r['metrics'].get(key) for r in entries if r['phase'] == phase]
            return f'{found[-1]:.4f}' if found and number(found[-1]) else 'unavailable'
        rows.append('<tr>' + ''.join(f'<td>{escape(str(x))}</td>' for x in [
            run, entries[-1]['phase'], json.dumps(config, sort_keys=True), value('baseline', 'baseline_title_match'),
            value('final', 'eval_title_match')]) + '</tr>')
    latest = max((e['timestamp'] for e in events), default='unavailable')
    return ('<!doctype html><meta charset="utf-8"><title>Training experiments</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem;color:#172033}svg{max-width:900px;width:100%}td,th{padding:.6rem;border-bottom:1px solid #ddd;text-align:left}td{overflow-wrap:anywhere}table{width:100%}li{font-size:13px}</style>'
            '<h1>Training experiments</h1>'
            f'<p>UTC snapshot. Latest event: {escape(latest)}. Malformed events: {malformed}. Scan limit reached: {limited}.</p>'
            '<p>Each series represents one run and metric; points are recorded epoch observations. Epoch 0 is the before-training baseline when available. '
            'Baseline is measured before training. Independent experiment runs load the pinned public T5 base; application training runs may load a previous fine-tuned checkpoint. '
            'Compare scores only when validation membership and decoding settings match. Missing files may indicate retention gaps.</p>'
            + ''.join(charts) + '<h2>Run comparison</h2><table><tr><th>Run</th><th>Latest phase</th><th>Configuration</th><th>Before: title accuracy</th><th>After: title accuracy</th></tr>'
            + ''.join(rows) + '</table>' + ('<p>No training events recorded.</p>' if not runs else ''))
