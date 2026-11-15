"""Independent fresh-base trials using frozen data and no application model state."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import tomllib
from uuid import uuid4

from model.experiment_base import BASE_MODEL, load_fresh_base
from model.experiment_data import digest
from model.experiment_metrics import ROOT, record_event, number, load_events, render
from model.training_data import title_accuracy, write_json


def load_config(path):
    config = tomllib.loads(path.read_text())
    for key in ['training_seed', 'training_size', 'epochs', 'batch_size',
                'gradient_accumulation_steps', 'eval_batch_size', 'input_max_length',
                'target_max_length', 'num_beams', 'cpu_threads']:
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < (0 if key == 'training_seed' else 1):
            raise ValueError(f'{key} must be a positive integer (seed may be zero)')
    for key in ['learning_rate', 'weight_decay']:
        if not number(config.get(key)) or config[key] < 0 or (key == 'learning_rate' and config[key] == 0):
            raise ValueError(f'{key} must be finite and nonnegative (learning rate must be positive)')
    if config.get('base_model') != BASE_MODEL:
        raise ValueError('base_model must be the public google-t5/t5-small')
    return config


def verify_data(config, root=ROOT):
    directory = root/config['output']
    manifest = json.loads((directory/'manifest.json').read_text())
    if digest(root/config['source']) != manifest['source_sha256']:
        raise ValueError('Source hash changed; create a new experiment split')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or digest(directory/name) != expected:
            raise ValueError('Frozen split file hash mismatch')
    if config['training_size'] not in manifest['training_sizes']:
        raise ValueError('training_size is not an available frozen subset')
    return directory, manifest


def run(config, mode, root=ROOT):
    import numpy as np
    import torch
    import transformers
    from datasets import Dataset
    from transformers import (DataCollatorForSeq2Seq, Seq2SeqTrainer,
                              Seq2SeqTrainingArguments, TrainerCallback, set_seed)

    if mode not in {'baseline', 'train'}:
        raise ValueError('mode must be baseline or train')
    directory, manifest = verify_data(config, root)
    run_id = f'{mode}-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
    output = directory/'runs'/run_id
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = dict(run_id=run_id, mode=mode, status='started', configuration=config,
                  base_model=config['base_model'], base_revision=config['base_revision'],
                  initialization='fresh_public_pretrained_weights', resumed_checkpoint=None,
                  manifest_sha256=digest(directory/'manifest.json'),
                  validation_sha256=manifest['files']['validation.jsonl'],
                  versions=dict(torch=torch.__version__, transformers=transformers.__version__))
    write_json(output/'report.json', report)
    record_event(run_id, 'started', {}, {**config, 'manifest_sha256': report['manifest_sha256']})
    try:
        torch.set_num_threads(config['cpu_threads'])
        set_seed(config['training_seed'])
        tokenizer, model = load_fresh_base(config['base_model'], config['base_revision'])
        def dataset(name):
            rows = [json.loads(line) for line in (directory/name).read_text().splitlines() if line.strip()]
            def tokenize(batch):
                return tokenizer([config['task_prefix']+text for text in batch['input']],
                                 text_target=batch['target'], truncation=True,
                                 max_length=config['input_max_length'])
            # Separate label bound avoids silently truncating targets to the input bound.
            def encode(batch):
                values = tokenize(batch)
                values['labels'] = tokenizer(text_target=batch['target'], truncation=True,
                                             max_length=config['target_max_length'])['input_ids']
                return values
            data = Dataset.from_list(rows)
            return data.map(encode, batched=True, remove_columns=data.column_names)

        def compute_metrics(prediction):
            predictions, labels = prediction
            if isinstance(predictions, tuple):
                predictions = predictions[0]
            return title_accuracy(
                tokenizer.batch_decode(np.where(predictions == -100, tokenizer.pad_token_id, predictions), skip_special_tokens=True),
                tokenizer.batch_decode(np.where(labels == -100, tokenizer.pad_token_id, labels), skip_special_tokens=True))

        class Callback(TrainerCallback):
            def on_log(self, args, state, control, logs=None, **kwargs):
                metrics = {k:v for k,v in (logs or {}).items() if number(v)}
                metrics.update(epoch=state.epoch or 0, step=state.global_step)
                record_event(run_id, 'progress', metrics)
                print(json.dumps(dict(run=run_id, **metrics)), flush=True)

        model.generation_config.min_new_tokens = 1
        args = Seq2SeqTrainingArguments(
            output_dir=str(output/'checkpoints'), num_train_epochs=config['epochs'],
            learning_rate=config['learning_rate'], weight_decay=config['weight_decay'],
            per_device_train_batch_size=config['batch_size'],
            gradient_accumulation_steps=config['gradient_accumulation_steps'],
            per_device_eval_batch_size=config['eval_batch_size'],
            eval_strategy='epoch', save_strategy='epoch', logging_strategy='epoch',
            load_best_model_at_end=True, metric_for_best_model='eval_title_match',
            greater_is_better=True, save_total_limit=1, report_to='none',
            seed=config['training_seed'], data_seed=config['training_seed'],
            predict_with_generate=True, generation_max_length=config['target_max_length'],
            generation_num_beams=config['num_beams'], disable_tqdm=True,
            dataloader_num_workers=0, dataloader_pin_memory=torch.cuda.is_available())
        trainer = Seq2SeqTrainer(model=model, args=args, processing_class=tokenizer,
            train_dataset=dataset(f"train_{config['training_size']}.jsonl") if mode == 'train' else None,
            eval_dataset=dataset('validation.jsonl'),
            data_collator=DataCollatorForSeq2Seq(tokenizer, model=model),
            compute_metrics=compute_metrics, callbacks=[Callback()])
        report['device'] = str(trainer.args.device)
        report['effective_batch_size'] = args.train_batch_size * args.gradient_accumulation_steps * args.world_size
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        report['baseline_metrics'] = trainer.evaluate(metric_key_prefix='baseline')
        record_event(run_id, 'baseline', {k:v for k,v in report['baseline_metrics'].items() if number(v)})
        write_json(output/'report.json', report)
        if mode == 'train':
            # Always create fresh optimizer/scheduler state. Never pass a checkpoint.
            trainer.train(resume_from_checkpoint=False)
            report['final_metrics'] = trainer.evaluate()
            record_event(run_id, 'final', {k:v for k,v in report['final_metrics'].items() if number(v)})
            report['best_checkpoint'] = trainer.state.best_model_checkpoint
            report['optimizer_steps'] = trainer.state.global_step
            trainer.save_model(str(output/'model'))
            tokenizer.save_pretrained(str(output/'model'))
        report['log_history'] = trainer.state.log_history
        report['status'] = 'success'
        report['duration_seconds'] = time.monotonic()-started
        if torch.cuda.is_available():
            report['peak_gpu_memory_bytes'] = torch.cuda.max_memory_allocated()
        record_event(run_id, 'completed', {'duration_seconds':report['duration_seconds']})
    except BaseException as error:
        report.update(status='failure', error_type=type(error).__name__, duration_seconds=time.monotonic()-started)
        record_event(run_id, 'failed', {'duration_seconds':report['duration_seconds']}, {'error_type':type(error).__name__})
        raise
    finally:
        write_json(output/'report.json', report)
        # A local snapshot is refreshed after each run, including failed runs.
        # Read payload-free training events from the last seven available daily files.
        paths = sorted((root/'logs').glob('????-??-??.log'))[-7:]
        events, malformed, limited = load_events(paths)
        matching = [e for e in events if e['run'].startswith(('baseline-', 'train-'))]
        (directory/'charts.html').write_text(render(matching, malformed, limited), encoding='utf-8')
    print(output/'report.json', flush=True)
    return report
