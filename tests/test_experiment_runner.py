"""Run the real independent pipeline offline with tiny synthetic T5 weights."""
import unittest
import test_runtime


class ExperimentRunnerTests(unittest.TestCase):
    def test_baseline_and_training_start_fresh(self):
        test_runtime.RuntimeTests().run_isolated('''
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
import json
from pathlib import Path
from unittest.mock import patch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import PreTrainedTokenizerFast, T5Config, T5ForConditionalGeneration
from model.experiment_data import prepare
from model.experiment_runner import run, verify_data
from model.experiment_metrics import record_event

root = Path.cwd()/'experiment'
root.mkdir()
source = root/'data.json'
source.write_text(json.dumps([dict(input=f'Movie {i}',target=f'Title {i}') for i in range(20)]))
prepare(source,root/'split',42,.2,.2,[4])
config = dict(source=str(source),output=str(root/'split'),base_model='google-t5/t5-small',
    base_revision='a'*40,task_prefix='clean the title: ',training_seed=42,training_size=4,
    epochs=1,learning_rate=.0001,weight_decay=.01,batch_size=2,gradient_accumulation_steps=2,
    eval_batch_size=2,input_max_length=8,target_max_length=8,num_beams=1,cpu_threads=2)
def fresh(*args):
    vocabulary = {'<pad>':0,'</s>':1,'<unk>':2,'Movie':3,'Title':4}
    raw = Tokenizer(WordLevel(vocabulary,unk_token='<unk>'))
    raw.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=raw,pad_token='<pad>',eos_token='</s>',unk_token='<unk>')
    model = T5ForConditionalGeneration(T5Config(vocab_size=len(vocabulary),d_model=16,d_ff=32,
        num_layers=1,num_decoder_layers=1,num_heads=2,decoder_start_token_id=0,pad_token_id=0,eos_token_id=1))
    return tokenizer, model
def event(*args,**kwargs):
    record_event(*args,**kwargs,root=root)
with patch('model.experiment_runner.load_fresh_base',side_effect=fresh) as loader, patch('model.experiment_runner.record_event',side_effect=event):
    baseline = run(config,'baseline',root=root)
    trained = run(config,'train',root=root)
assert loader.call_count == 2
assert baseline['status'] == trained['status'] == 'success'
assert baseline['baseline_metrics']['baseline_loss'] == trained['baseline_metrics']['baseline_loss']
assert trained['resumed_checkpoint'] is None
assert trained['effective_batch_size'] == 4
assert Path(trained['best_checkpoint']).exists()
assert 'eval_title_match' in trained['final_metrics']
with (root/'split/validation.jsonl').open('a') as f:f.write(' ')
try:
    verify_data(config)
except ValueError:
    pass
else:
    raise AssertionError('Changed validation was accepted')
''')
