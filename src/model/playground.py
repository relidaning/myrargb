"""Configured model selection with one cached experimental model and a request lock."""
import gc
from pathlib import Path
from threading import Lock
import time
import tomllib

from model.model import model
from model.prediction import predict_text,PredictionBusyError
from utils.settings import settings,PROJECT_ROOT
from utils.job_metrics import record_job


class PredictionPlayground:
    def __init__(self):
        self.lock=Lock()
        self.cached=None
        self.cached_key=None
        self.cached_signature=None

    def options(self):
        choices=[dict(id='current',label='Application model',path=settings.model_path)]
        if settings.prediction_models_path.exists():
            config=tomllib.loads(settings.prediction_models_path.read_text())
            for index,entry in enumerate(config.get('models',[])[1:],1):
                choices.append(dict(id=f'candidate-{index}',label=entry['label'],path=PROJECT_ROOT/entry['path']))
        for choice in choices:
            choice['available']=(choice['path']/'config.json').is_file() and (choice['path']/'model.safetensors').is_file()
        return choices

    def predict(self,text,selected):
        choices={choice['id']:choice for choice in self.options()}
        if not selected or len(selected)!=len(set(selected)) or any(key not in choices for key in selected):
            raise ValueError('Select one or more available models.')
        if any(not choices[key]['available'] for key in selected):
            raise FileNotFoundError('A selected model is not installed locally.')
        if not self.lock.acquire(blocking=False):
            raise PredictionBusyError('Another prediction is running. Please try again shortly.')
        try:
            results=[]
            for key in selected:
                choice=choices[key]
                started=time.monotonic()
                outcome='failure'
                try:
                    if key=='current':
                        title=model.predict_text(text)
                    else:
                        weights=choice['path']/'model.safetensors'
                        signature=(str(choice['path'].resolve()),weights.stat().st_mtime_ns,weights.stat().st_size)
                        if self.cached_key!=key or self.cached_signature!=signature:
                            self.cached=None
                            self.cached_key=None
                            gc.collect()
                            from transformers import AutoModelForSeq2SeqLM,AutoTokenizer
                            path=str(choice['path'])
                            tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,token=False,trust_remote_code=False)
                            loaded=AutoModelForSeq2SeqLM.from_pretrained(path,local_files_only=True,token=False,trust_remote_code=False)
                            self.cached=(tokenizer,loaded)
                            self.cached_key=key
                            self.cached_signature=signature
                        title=predict_text(*self.cached,text,settings.prediction_max_input_tokens,
                                           settings.generation_min_new_tokens)
                    elapsed=time.monotonic()-started
                    results.append(dict(model=key,label=choice['label'],title=title,duration_ms=round(elapsed*1000,1)))
                    outcome='success'
                finally:
                    record_job('prediction_playground:'+key,outcome,time.monotonic()-started)
            return results
        finally:
            self.lock.release()


playground=PredictionPlayground()
