"""Plan isolated serial trials and visualize their logged validation results."""
import hashlib
from html import escape
import itertools
import json
from statistics import mean, stdev

from model.experiment_metrics import number


def build_plan(base, grid, manifest_sha256):
    seed_mode = 'training_seeds' in grid
    if seed_mode:
        pairs = [(setting['learning_rate'],setting['effective_batch_size']) for setting in grid['settings']]
        rates = list(dict.fromkeys(rate for rate,_ in pairs))
        batches = list(dict.fromkeys(batch for _,batch in pairs))
        seeds = grid['training_seeds']
        if not seeds or any(not isinstance(s,int) or isinstance(s,bool) or not 0 <= s < 2**32 for s in seeds) or len(set(seeds))!=len(seeds):
            raise ValueError('training_seeds must contain distinct integers in [0, 2**32)')
        if len(set(pairs))!=len(pairs):
            raise ValueError('settings must be distinct')
    else:
        rates, batches = grid['learning_rates'], grid['effective_batch_sizes']
        pairs = list(itertools.product(rates,batches))
        seeds = [grid['training_seed']]
    if not rates or any(not number(v) or v <= 0 for v in rates) or len(set(rates)) != len(rates):
        raise ValueError('learning_rates must contain distinct positive finite numbers')
    if not batches or any(not isinstance(v,int) or isinstance(v,bool) or v < 1
                          or v % base['batch_size'] for v in batches) or len(set(batches)) != len(batches):
        raise ValueError('effective_batch_sizes must be distinct positive multiples of batch_size')
    trials = []
    for (rate, batch), seed in itertools.product(pairs,seeds):
        config = {**base, **{key:grid[key] for key in ['training_size','epochs']}, 'training_seed':seed,
                  'learning_rate':rate, 'gradient_accumulation_steps':batch//base['batch_size']}
        suffix=f'-seed-{seed}' if seed_mode else ''
        trials.append(dict(id=f'lr-{rate:g}-batch-{batch}{suffix}',effective_batch_size=batch,configuration=config))
    identity = json.dumps(dict(trials=trials,manifest_sha256=manifest_sha256),sort_keys=True)
    sweep_id = hashlib.sha256(identity.encode()).hexdigest()[:16]
    for trial in trials:
        trial['configuration'].update(sweep_id=sweep_id,trial_id=trial['id'])
    plan = dict(sweep_id=sweep_id,manifest_sha256=manifest_sha256,trials=trials)
    if seed_mode:
        plan['kind']='seed_confirmation'
    return plan


def successful_report(directory, config, manifest_sha256, ignore_tracking=False):
    def comparable(values):
        return {key:value for key,value in values.items() if not ignore_tracking or key not in {'sweep_id','trial_id'}}
    for path in sorted((directory/'runs').glob('*/report.json'),reverse=True):
        try:
            report=json.loads(path.read_text())
            if (report.get('status')=='success' and isinstance(report.get('configuration'),dict)
                    and comparable(report['configuration'])==comparable(config)
                    and (not ignore_tracking or (report.get('initialization')=='fresh_public_pretrained_weights'
                                                and report.get('resumed_checkpoint') is None))
                    and report.get('manifest_sha256')==manifest_sha256 and report.get('mode')=='train'):
                return report
        except (ValueError,OSError):
            continue
    return None


def toml_text(config):
    # Flat settings only: JSON strings, finite numbers and arrays are TOML-compatible.
    return ''.join(f'{key} = {json.dumps(value,ensure_ascii=False,allow_nan=False)}\n'
                   for key,value in config.items())


def seed_statistics(plan,events):
    observed={}
    for event in events:
        config=event.get('config',{})
        if config.get('sweep_id')==plan['sweep_id'] and event['phase'] in {'sweep_trial_completed','sweep_trial_failed'}:
            observed[config['trial_id']]=event
    groups={}
    for trial in plan['trials']:
        config=trial['configuration']
        key=(config['learning_rate'],trial['effective_batch_size'])
        group=groups.setdefault(key,dict(learning_rate=key[0],effective_batch_size=key[1],observations=[],expected_seeds=[]))
        seed=config['training_seed']
        group['expected_seeds'].append(seed)
        event=observed.get(trial['id'])
        metrics=event['metrics'] if event else {}
        score=metrics.get('eval_title_match')
        group['observations'].append(dict(seed=seed,status='success' if number(score) else ('failure' if event else 'pending'),
                                         title_match=score if number(score) else None,
                                         exact_match=metrics.get('eval_exact_match'),
                                         run_id=event['config'].get('run_id') if event else None))
    for group in groups.values():
        scores=[o['title_match'] for o in group['observations'] if o['title_match'] is not None]
        exact=[o['exact_match'] for o in group['observations'] if number(o['exact_match'])]
        group.update(completed=len(scores),mean=mean(scores) if scores else None,
                     sample_sd=stdev(scores) if len(scores)>1 else None,
                     minimum=min(scores) if scores else None,maximum=max(scores) if scores else None,
                     mean_exact_match=mean(exact) if exact else None)
    return list(groups.values())


def render_seed_comparison(plan,events):
    groups=seed_statistics(plan,events)
    latest=max((event.get('timestamp','unavailable') for event in events
                if event.get('config',{}).get('sweep_id')==plan['sweep_id']),default='unavailable')
    rows,marks=[],[]
    def pct(value):
        return f'{value:.1%}' if number(value) else 'unavailable'
    for index,group in enumerate(groups):
        label=f"LR {group['learning_rate']:g}, batch {group['effective_batch_size']}"
        observations='; '.join(f"{o['seed']}: {pct(o['title_match']) if o['status']=='success' else o['status']}" for o in group['observations'])
        sd=f"{100*group['sample_sd']:.2f} pp" if number(group['sample_sd']) else 'unavailable'
        values=[label,observations,f"{group['completed']}/{len(group['expected_seeds'])}",pct(group['mean']),sd,
                f"{pct(group['minimum'])} – {pct(group['maximum'])}",pct(group['mean_exact_match'])]
        rows.append('<tr>'+''.join(f'<td>{escape(value)}</td>' for value in values)+'</tr>')
        y=50+index*75
        marks.append(f'<text x="10" y="{y}">{escape(label)}</text>')
        if group['completed']:
            center=300+group['mean']*500
            spread=(group['sample_sd'] or 0)*500
            marks.append(f'<line x1="{max(300,center-spread):.2f}" x2="{min(800,center+spread):.2f}" y1="{y}" y2="{y}" stroke="#172033" stroke-width="3"/>')
            marks.append(f'<rect x="{center-4:.2f}" y="{y-4}" width="8" height="8" fill="#172033"><title>Mean: {pct(group["mean"])}</title></rect>')
        for j,o in enumerate(group['observations']):
            if o['title_match'] is not None:
                color=['#2563eb','#b45309','#047857'][j%3]
                marks.append(f'<circle cx="{300+o["title_match"]*500:.2f}" cy="{y-13+j*13}" r="5" fill="{color}"><title>Seed {o["seed"]}: {pct(o["title_match"])}</title></circle>')
    axis=75*len(groups)+25
    for tick in [0,.25,.5,.75,1]:
        x=300+500*tick
        marks.append(f'<text x="{x}" y="{axis}">{tick:.0%}</text>')
    return ('<!doctype html><meta charset="utf-8"><title>T5 seed comparison</title>'
            '<style>body{font:16px system-ui;max-width:1200px;margin:2rem auto;padding:1rem;color:#172033}td,th{padding:.6rem;text-align:left;border-bottom:1px solid #ddd}svg{width:100%;max-width:1000px}table{width:100%}</style>'
            '<h1>T5 seed comparison</h1>'
            f'<p>Sweep {escape(plan["sweep_id"])}. Fixed training/validation partitions and base weights; only training seeds vary within each setting.</p>'
            f'<p>Latest matching log event (UTC): {escape(latest)}.</p>'
            '<p>Dots are individual validation title accuracies. Squares show means; lines show ±1 sample standard deviation (SD), not confidence intervals. '
            'SD uses n−1 and is unavailable with fewer than two observations. Statistics use successful runs only; completed/expected counts expose missing trials. '
            'Seed 42 was used to select these candidates; the other seeds provide additional observations. Three seeds give only a preliminary variability estimate. '
            'The test partition is not evaluated. Seeds are repetitions, not hyperparameters to select by their best result.</p>'
            f'<svg viewBox="0 0 850 {axis+35}" role="img" aria-label="Validation accuracy across training seeds">'+''.join(marks)+'</svg>'
            '<table><tr><th>Setting</th><th>Seed: title accuracy</th><th>Completed</th><th>Mean title accuracy</th><th>Sample SD (percentage points)</th><th>Range</th><th>Mean exact match</th></tr>'
            +''.join(rows)+'</table><p>Snapshot from the last seven available daily logs. '
            '<a href="../../charts.html">Epoch curves and full configurations</a></p>')


def render_comparison(plan, events):
    observed = {}
    for event in events:
        config = event.get('config',{})
        if config.get('sweep_id') == plan['sweep_id'] and event['phase'] in {'sweep_trial_completed','sweep_trial_failed'}:
            observed[config['trial_id']] = event
    latest=max((event.get('timestamp','unavailable') for event in observed.values()),default='unavailable')
    rows, cells = [], []
    for trial in plan['trials']:
        event=observed.get(trial['id'])
        metrics=event['metrics'] if event else {}
        score=metrics.get('eval_title_match')
        config=trial['configuration']
        label=f"LR {config['learning_rate']:g}, batch {trial['effective_batch_size']}"
        if number(score):
            hue=140*max(0,min(1,score))
            cells.append(f'<div class="cell" style="background:hsl({hue:.0f} 65% 90%)">{escape(label)}<strong>{score:.1%}</strong></div>')
        else:
            cells.append(f'<div class="cell">{escape(label)}<strong>{"failed" if event else "pending"}</strong></div>')
        def display(key,percentage=False):
            value=metrics.get(key)
            return (f'{value:.1%}' if percentage else f'{value:.3f}') if number(value) else 'unavailable'
        values=[label,'success' if number(score) else ('failure' if event else 'pending'),
                display('eval_title_match',True),display('eval_exact_match',True),
                display('eval_loss'),display('duration_seconds'),display('optimizer_steps')]
        rows.append('<tr>'+''.join(f'<td>{escape(value)}</td>' for value in values)+'</tr>')
    return ('<!doctype html><meta charset="utf-8"><title>T5 hyperparameter screen</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem;color:#172033}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.cell{padding:1rem;border:1px solid #ccd4df;border-radius:8px}.cell strong{display:block;font-size:26px;margin-top:8px}td,th{padding:.6rem;text-align:left;border-bottom:1px solid #ddd}table{width:100%}</style>'
            f'<h1>T5 hyperparameter screen</h1><p>Sweep {escape(plan["sweep_id"])}. '
            'Validation title accuracy (0–100%). Each cell uses the selected checkpoint from one fresh-base trial.</p>'
            f'<p>Latest completion event (UTC): {escape(latest)}. Snapshot reads the last seven available daily logs.</p>'
            '<p>Five-epoch screen unless changed in the saved plan. One seed; differences need confirmation across seeds. '
            'Equal epochs mean equal data exposure, but larger effective batches perform fewer optimizer updates. '
            'Test data is excluded from model selection. Missing observations remain pending/unavailable.</p>'
            '<div class="grid">'+''.join(cells)+'</div><h2>Recorded results</h2><table><tr><th>Setting</th><th>Status</th><th>Title accuracy</th><th>Exact match</th><th>Loss</th><th>Wall time (s)</th><th>Optimizer steps</th></tr>'
            +''.join(rows)+'</table><p><a href="../../charts.html">Epoch curves and full configurations</a></p>')
