"""Read-only comparison of local models using one common generation protocol."""
from datetime import datetime,timezone
from html import escape
import json
from pathlib import Path
import time
from uuid import uuid4

from model.experiment_data import digest
from model.experiment_metrics import ROOT,record_event
from model.experiment_runner import verify_data
from model.training_data import title_accuracy,write_json


def render_comparison(events,comparison_id):
    results={}
    for event in events:
        if event.get('config',{}).get('comparison_id')==comparison_id and event['phase']=='comparison_model_completed':
            results[event['config']['label']]=event
    rows=[]
    for label,event in results.items():
        m=event['metrics']
        rows.append('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in [label,
            f"{m['title_match']:.1%}",f"{m['exact_match']:.1%}",
            f"{m['correct_titles']}/{m['examples']}"])
            +f'<td><meter min="0" max="1" value="{m["title_match"]}">{m["title_match"]:.1%}</meter></td></tr>')
    return ('<!doctype html><meta charset="utf-8"><title>Existing versus new T5</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}td,th{padding:.7rem;text-align:left;border-bottom:1px solid #ddd}meter{width:200px}</style>'
            '<h1>Existing versus new T5</h1><p>Same frozen validation examples and common deterministic decoding. '
            'Title accuracy ignores case, spaces and periods; strict accuracy normalizes whitespace only. '
            'The existing model’s historical training exposure is unknown; this is not an unbiased comparison on unseen titles. '
            'New candidates were selected using validation. Test examples are not evaluated.</p>'
            f'<p>Comparison: {escape(comparison_id)}. UTC snapshot from daily logs.</p>'
            '<table><tr><th>Model</th><th>Title accuracy</th><th>Strict accuracy</th><th>Correct titles</th><th>Title accuracy (0–100%)</th></tr>'
            +''.join(rows)+'</table>')


def compare(config,experiment,root=ROOT):
    import gc
    import torch
    from transformers import AutoTokenizer,AutoModelForSeq2SeqLM,GenerationConfig

    for key in ['batch_size','cpu_threads','input_max_length','max_new_tokens','num_beams']:
        if not isinstance(config.get(key),int) or isinstance(config[key],bool) or config[key]<1:
            raise ValueError(f'{key} must be a positive integer')
    if type(config.get('min_new_tokens')) is not int or not 0<=config['min_new_tokens']<=config['max_new_tokens']:
        raise ValueError('min_new_tokens must be between zero and max_new_tokens')
    if type(config.get('early_stopping')) is not bool:
        raise ValueError('early_stopping must be a boolean')
    length_parameter=config.get('length_parameter','max_new_tokens')
    if length_parameter not in {'max_new_tokens','max_length'}:
        raise ValueError('length_parameter must be max_new_tokens or max_length')
    if not config.get('models') or len({entry['label'] for entry in config['models']})!=len(config['models']):
        raise ValueError('models must be nonempty with distinct labels')
    directory,manifest=verify_data(experiment,root)
    rows=[json.loads(line) for line in (directory/'validation.jsonl').read_text().splitlines()]
    comparison_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8]
    output=directory/'comparisons'/comparison_id
    output.mkdir(parents=True,exist_ok=False)
    report=dict(comparison_id=comparison_id,status='started',configuration=config,
                manifest_sha256=digest(directory/'manifest.json'),
                validation_sha256=manifest['files']['validation.jsonl'],test_evaluated=False,
                historical_exposure_of_existing_model='unknown',results=[])
    torch.set_num_threads(config['cpu_threads'])
    device='cuda' if torch.cuda.is_available() else 'cpu'
    report['device']=device
    try:
        for entry in config['models']:
            started=time.monotonic()
            path=(root/entry['path']).resolve(strict=True)
            weights=path/'model.safetensors'
            fingerprint=digest(weights)
            tokenizer=AutoTokenizer.from_pretrained(str(path),local_files_only=True,token=False,trust_remote_code=False)
            model=AutoModelForSeq2SeqLM.from_pretrained(str(path),local_files_only=True,token=False,trust_remote_code=False)
            model.to(device).eval()
            generation=GenerationConfig(**{length_parameter:config['max_new_tokens']},min_new_tokens=config['min_new_tokens'],
                num_beams=config['num_beams'],early_stopping=config['early_stopping'],do_sample=False,
                decoder_start_token_id=model.config.decoder_start_token_id,
                pad_token_id=tokenizer.pad_token_id,eos_token_id=tokenizer.eos_token_id)
            predictions=[]
            with torch.inference_mode():
                for offset in range(0,len(rows),config['batch_size']):
                    batch=rows[offset:offset+config['batch_size']]
                    inputs=tokenizer([experiment['task_prefix']+r['input'] for r in batch],
                                     return_tensors='pt',padding=True,truncation=True,max_length=config['input_max_length'],
                                     return_token_type_ids=False).to(device)
                    generated=model.generate(**inputs,generation_config=generation)
                    predictions.extend(tokenizer.batch_decode(generated,skip_special_tokens=True))
            metrics=title_accuracy(predictions,[r['target'] for r in rows])
            metrics.update(examples=len(rows),correct_titles=round(metrics['title_match']*len(rows)),duration_seconds=time.monotonic()-started)
            assert digest(weights)==fingerprint, 'Model weights changed during comparison'
            report['results'].append(dict(label=entry['label'],model_path=str(path),weights_sha256=fingerprint,metrics=metrics))
            record_event(comparison_id,'comparison_model_completed',metrics,
                         {'comparison_id':comparison_id,'label':entry['label']},root=root)
            write_json(output/'report.json',report)
            print(json.dumps(dict(label=entry['label'],**metrics)),flush=True)
            del model,tokenizer,inputs,generated
            gc.collect()
            if torch.cuda.is_available():torch.cuda.empty_cache()
        report['status']='success'
    except BaseException as error:
        report.update(status='failure',error_type=type(error).__name__)
        record_event(comparison_id,'comparison_failed',{}, {'error_type':type(error).__name__},root=root)
        raise
    finally:
        write_json(output/'report.json',report)
        from model.experiment_metrics import load_events
        events,bad,limited=load_events(sorted((root/'logs').glob('????-??-??.log'))[-7:])
        (output/'comparison.html').write_text(render_comparison(events,comparison_id),encoding='utf-8')
    print(output/'comparison.html',flush=True)
    return report
