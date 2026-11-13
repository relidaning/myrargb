"""Training data, persistent holdouts, metrics, and route bookkeeping."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from db_model import Movie
from model.training_data import clean_target, prepare_split, title_accuracy, read_manifest, write_json
from utils.settings import load_settings
import test_runtime


class TrainingDataTests(unittest.TestCase):
    def items(self):
        return [Movie(id=i, filename=f'Title{i}.2020.1080p', title_accurate=f'Title {i}',
                      trained_flag='1' if i % 2 else '0') for i in range(1, 31)]

    def test_cleanup_and_empty_examples(self):
        self.assertEqual(clean_target('  The..Matrix.\t '), 'The Matrix')
        self.assertEqual(clean_target(None), '')
        items = self.items() + [Movie(id=99, filename='bad', title_accurate='...')]
        split = prepare_split(items, 42, .2)
        self.assertEqual(split.skipped_ids, [99])
        self.assertEqual(sum(map(len, [split.train, split.validation])), 30)
        self.assertEqual(items[0].filename, 'Title1.2020.1080p')

    def test_grouped_split_is_reproducible_and_persistent(self):
        items = self.items()
        first = prepare_split(items, 42, .2)
        reordered = prepare_split(items[::-1], 42, .2)
        self.assertEqual(first.summary(), reordered.summary())
        held = first.validation[0]
        variants = [Movie(id=99, filename='Other release', title_accurate=held['clean'].upper().replace(' ', '.')),
                    Movie(id=100, filename='New release', title_accurate='Entirely New Title')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'split.json'
            write_json(path, first.manifest)
            again = prepare_split(items + variants, 42, .2, read_manifest(path))
        self.assertIn(99, [row['id'] for row in again.validation])
        self.assertIn(100, [row['id'] for row in again.train])
        self.assertEqual(first.manifest['validation_groups'], again.manifest['validation_groups'])
        self.assertFalse(set(row['id'] for row in again.train) & set(row['id'] for row in again.validation))
        with self.assertRaises(ValueError):
            prepare_split(items, 43, .2, first.manifest)
        with self.assertRaises(ValueError):
            prepare_split([items[0]], 42, .2)

    def test_tv_seasons_and_episodes_share_a_group(self):
        items = self.items() + [
            Movie(id=80, filename='Show.S01E01', title_accurate='Show S01E01'),
            Movie(id=81, filename='Show.S02E01', title_accurate='Show S02E01'),
        ]
        split = prepare_split(items, 42, .2)
        for rows in [split.train, split.validation]:
            ids = {row['id'] for row in rows}
            self.assertEqual(80 in ids, 81 in ids)

    def test_generated_metrics(self):
        self.assertEqual(title_accuracy(['The Matrix', 'the.matrix', 'wrong'],
                                        ['The Matrix', 'The Matrix', 'Other']),
                         {'exact_match': 1/3, 'title_match': 2/3})
        with self.assertRaises(ValueError):
            title_accuracy([], [])

    def test_training_settings_precedence_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'training.toml'
            path.write_text('training_seed = 12\ntraining_validation_fraction = 0.3\n')
            cfg = load_settings({'MYRARGB_CONFIG': str(path), 'MYRARGB_TRAINING_SEED': '0'})
            self.assertEqual(cfg.training_seed, 0)
            self.assertEqual(cfg.training_validation_fraction, .3)
        for name, value in [('training_seed', '-1'), ('training_seed', '4294967296'), ('training_validation_fraction', '1'),
                            ('training_validation_fraction', '0'), ('training_epochs', '0'),
                            ('training_batch_size', '0'), ('training_max_length', '1')]:
            with self.assertRaisesRegex(ValueError, name):
                load_settings({'MYRARGB_' + name.upper(): value})

    def test_real_seq2seq_training_with_temporary_random_model(self):
        test_runtime.RuntimeTests().run_isolated('''
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import PreTrainedTokenizerFast, T5Config, T5ForConditionalGeneration
from model.model import MyRargbModel
from utils.settings import settings
from db_model import Movie

torch.set_num_threads(2)
root = settings.database_path.parent / 'synthetic'
cfg = replace(settings, training_split_path=root/'split.json', training_output_path=root/'runs',
              training_log_path=root/'logs', training_epochs=1, training_batch_size=4,
              training_max_length=12, training_num_beams=1)
vocabulary = {'<pad>':0, '</s>':1, '<unk>':2, 'Movie':3, 'clean':4, 'the':5, 'title':6}
vocabulary.update({str(i):len(vocabulary)+i-1 for i in range(1,13)})
raw_tokenizer = Tokenizer(WordLevel(vocabulary, unk_token='<unk>'))
raw_tokenizer.pre_tokenizer = Whitespace()
instance = MyRargbModel.__new__(MyRargbModel)
instance.tokenizer = PreTrainedTokenizerFast(tokenizer_object=raw_tokenizer,
    pad_token='<pad>', eos_token='</s>', unk_token='<unk>')
instance.model = T5ForConditionalGeneration(T5Config(vocab_size=len(instance.tokenizer),
    d_model=16,d_ff=32,num_layers=1,num_decoder_layers=1,num_heads=2,
    decoder_start_token_id=0,pad_token_id=0,eos_token_id=1))
instance.local_model_path = str(root/'saved')
items = [Movie(id=i, filename=f'Movie.{i}.2020.1080p', title_accurate=f'Movie.{i}.',
               trained_flag='0') for i in range(1,13)]
with patch('model.model.settings',cfg):
    report = instance.train(items)
assert report['training_examples'] + report['validation_examples'] == 12
assert 'baseline_exact_match' in report['baseline_metrics']
assert 'eval_title_match' in report['final_metrics']
restored = T5ForConditionalGeneration.from_pretrained(report['best_checkpoint'],local_files_only=True)
assert all(torch.equal(a.cpu(),b.cpu()) for a,b in zip(instance.model.parameters(),restored.parameters()))
assert (root/'saved/model.safetensors').exists()
assert (Path(report['run_path'])/'report.json').exists()
''')

    def test_training_route_replays_old_corrections_without_a_row_limit(self):
        test_runtime.RuntimeTests().run_isolated('''
from unittest.mock import patch
import app
from db.db import db
from db_model import Movie
with db.conn:
    db.conn.executemany("INSERT INTO items(filename,title_accurate,trained_flag) VALUES(?,?,'1')",
                        [(f'File {i}',f'Title {i}') for i in range(1005)])
assert len(app.service.get_training_items()) == 1005
repo = app.movieRepository
pending = repo.insert(Movie(filename='New',title_accurate='New',trained_flag='0'))
held = repo.insert(Movie(filename='Held',title_accurate='Held',trained_flag='0'))
changed = repo.insert(Movie(filename='Changed',title_accurate='Before',trained_flag='0'))
from model.training_data import write_json
from utils.settings import settings
from bs4 import BeautifulSoup
write_json(settings.training_split_path, {'version':1, 'seed':settings.training_seed,
    'validation_fraction':settings.training_validation_fraction, 'validation_groups':['held']})
def button():
    return BeautifulSoup(app.app.test_client().get('/').data,'html.parser').find('button',onclick='train()')
assert button().find(class_='badge').text == '2'

def train(items):
    assert len(items) == 1008
    repo.update(Movie(id=changed,title_accurate='Concurrent correction'))
    return {'training_ids':[1,pending,changed], 'validation_ids':[held]}
with patch.object(app.model,'train',side_effect=train):
    response = app.app.test_client().post('/model/train')
assert response.status_code == 200, response.data
assert repo.find_one(pending).trained_flag == '1'
assert repo.find_one(held).trained_flag == '0'
assert repo.find_one(changed).trained_flag == '0'
assert button().find(class_='badge').text == '1'
repo.update(Movie(id=changed,trained_flag='1'))
assert button().find(class_='badge').text == '0'
assert button().has_attr('disabled')
assert '1 held out for validation' in button()['title']

with patch.object(app.model,'train',side_effect=ValueError('Not enough titles')):
    assert app.app.test_client().post('/model/train').status_code == 400
''')


if __name__ == '__main__':
    unittest.main()
