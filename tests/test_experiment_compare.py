import unittest
import test_runtime


class ModelComparisonTests(unittest.TestCase):
    def test_local_readonly_comparison_uses_same_protocol(self):
        test_runtime.RuntimeTests().run_isolated('''
import os
os.environ['CUDA_VISIBLE_DEVICES']=''
os.environ['HF_HUB_OFFLINE']='1'
import json
from pathlib import Path
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import PreTrainedTokenizerFast,T5Config,T5ForConditionalGeneration
from model.experiment_data import prepare,digest
from model.experiment_compare import compare,render_comparison
root=Path.cwd()/'comparison';root.mkdir()
source=root/'data.json'
source.write_text(json.dumps([dict(input=f'Movie {i}',target=f'Title {i}') for i in range(20)]))
prepare(source,root/'split',42,.2,.2,[4])
raw=Tokenizer(WordLevel({'<pad>':0,'</s>':1,'<unk>':2,'Movie':3,'Title':4},unk_token='<unk>'))
raw.pre_tokenizer=Whitespace()
tokenizer=PreTrainedTokenizerFast(tokenizer_object=raw,pad_token='<pad>',eos_token='</s>',unk_token='<unk>')
model=T5ForConditionalGeneration(T5Config(vocab_size=5,d_model=16,d_ff=32,num_layers=1,
    num_decoder_layers=1,num_heads=2,decoder_start_token_id=0,pad_token_id=0,eos_token_id=1))
for name in ['old','new']:
    model.save_pretrained(root/name);tokenizer.save_pretrained(root/name)
before=digest(root/'old/model.safetensors')
config=dict(batch_size=2,cpu_threads=2,input_max_length=8,max_new_tokens=8,min_new_tokens=1,
            num_beams=2,early_stopping=True,
            models=[dict(label=name,path=name) for name in ['old','new']])
experiment=dict(source=str(source),output=str(root/'split'),training_size=4,task_prefix='clean the title: ')
report=compare(config,experiment,root=root)
assert report['status']=='success' and not report['test_evaluated']
a,b=[r['metrics'] for r in report['results']]
assert a['title_match']==b['title_match'] and a['exact_match']==b['exact_match']
assert digest(root/'old/model.safetensors')==before
assert report['historical_exposure_of_existing_model']=='unknown'
assert len(list((root/'logs').glob('*.log')))==1
assert list((root/'split/comparisons').glob('*/comparison.html'))
''')
