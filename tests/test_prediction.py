import unittest
import test_runtime


class PredictionTests(unittest.TestCase):
    def test_candidate_cache_reloads_when_checkpoint_changes(self):
        from dataclasses import replace
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from model.playground import PredictionPlayground,settings
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'candidate';path.mkdir()
            (path/'config.json').write_text('{}')
            weights=path/'model.safetensors';weights.write_bytes(b'synthetic')
            config=root/'models.toml'
            config.write_text('models = [{label="Old",path="old"},{label="Candidate",path="candidate"}]\n')
            cfg=replace(settings,model_path=root/'old',prediction_models_path=config)
            with patch('model.playground.settings',cfg),patch('model.playground.PROJECT_ROOT',root), \
                 patch('model.playground.record_job'),patch('model.playground.predict_text',return_value='Title'), \
                 patch('transformers.AutoTokenizer.from_pretrained') as tokenizer, \
                 patch('transformers.AutoModelForSeq2SeqLM.from_pretrained') as loader:
                service=PredictionPlayground()
                service.predict('Input',['candidate-1'])
                service.predict('Input',['candidate-1'])
                self.assertEqual(loader.call_count,1)
                weights.write_bytes(b'changed synthetic weights')
                service.predict('Input',['candidate-1'])
                self.assertEqual(loader.call_count,2)
                self.assertEqual(tokenizer.call_args.kwargs['token'],False)
                self.assertTrue(loader.call_args.kwargs['local_files_only'])

    def test_routes_validation_navigation_and_payload_free_metrics(self):
        test_runtime.RuntimeTests().run_isolated('''
from unittest.mock import patch
from pathlib import Path
import app
from model.playground import playground
from model.prediction import PredictionBusyError
from utils.settings import settings
client=app.app.test_client()
with patch.object(playground,'options',return_value=[dict(id='current',label='Application model',available=True,path=Path('/unused'))]):
    page=client.get('/model/playground')
assert page.status_code==200 and b'prediction-text' in page.data and b'/static/js/prediction.js' in page.data
assert b'/model/playground' in client.get('/').data
assert b'/model/playground' in client.get('/tv').data
with patch.object(playground,'predict',return_value=[dict(model='current',label='Application model',title='The Matrix',duration_ms=1)]) as predict:
    response=client.post('/model/predict',json=dict(text='  PRIVATE_TEST_SENTINEL  '))
    assert response.status_code==200 and response.json['results'][0]['title']=='The Matrix'
    predict.assert_called_once_with('PRIVATE_TEST_SENTINEL',['current'])
    for payload in [None,[],{},dict(text=' '),dict(text=3),dict(text='x'*4097),dict(text='x',models='current'),dict(text='x',models=[3])]:
        assert client.post('/model/predict',json=payload).status_code==400
assert client.post('/model/predict',data='{bad',content_type='application/json').status_code==400
assert client.post('/model/predict',json=dict(text='x',models=['../../other'])).status_code==400
assert client.post('/model/predict',json=dict(text='x',models=[])).status_code==400
with patch.object(playground,'predict',side_effect=PredictionBusyError()):
    assert client.post('/model/predict',json=dict(text='x')).status_code==409
with patch.object(playground,'predict',side_effect=RuntimeError('internal detail')):
    response=client.post('/model/predict',json=dict(text='x'))
    assert response.status_code==500 and b'internal detail' not in response.data
logs=settings.log_path.read_text()
assert 'prediction_playground' in logs and 'PRIVATE_TEST_SENTINEL' not in logs
''')

    def test_inference_restores_mode_and_enforces_token_limit(self):
        test_runtime.RuntimeTests().run_isolated('''
import os
os.environ['CUDA_VISIBLE_DEVICES']=''
os.environ['HF_HUB_OFFLINE']='1'
import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import PreTrainedTokenizerFast,T5Config,T5ForConditionalGeneration
from model.prediction import predict_text,PredictionBusyError
from model.model import LazyModel
torch.set_num_threads(2)
raw=Tokenizer(WordLevel({'<pad>':0,'</s>':1,'<unk>':2,'Matrix':3},unk_token='<unk>'))
raw.pre_tokenizer=Whitespace()
tokenizer=PreTrainedTokenizerFast(tokenizer_object=raw,pad_token='<pad>',eos_token='</s>',unk_token='<unk>')
model=T5ForConditionalGeneration(T5Config(vocab_size=4,d_model=16,d_ff=32,num_layers=1,
    num_decoder_layers=1,num_heads=2,decoder_start_token_id=0,pad_token_id=0,eos_token_id=1))
assert model.training
first=predict_text(tokenizer,model,'Matrix',256,1)
assert model.training
assert predict_text(tokenizer,model,'Matrix',256,1)==first
model.eval()
predict_text(tokenizer,model,'Matrix',256,1)
assert not model.training
try:
    predict_text(tokenizer,model,'Matrix '*30,4,1)
except ValueError:
    pass
else:
    raise AssertionError('Input was silently truncated')
lazy=LazyModel();lazy._operation_lock.acquire()
try:
    lazy.predict_text('x')
except PredictionBusyError:
    pass
else:
    raise AssertionError('Concurrent training/inference was allowed')
finally:
    lazy._operation_lock.release()
assert lazy._instance is None
''')
