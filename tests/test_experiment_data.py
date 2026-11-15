import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from model.experiment_data import prepare, digest
from model.experiment_base import load_fresh_base


class ExperimentDataTests(unittest.TestCase):
    def test_fresh_base_rejects_local_weights_and_mutable_revision(self):
        for name, revision in [('data/my_finetuned_t5', 'a'*40),
                               ('google-t5/t5-small','main'),
                               ('google-t5/t5-small','')]:
            with self.assertRaises(ValueError):
                load_fresh_base(name,revision)

    def test_fresh_base_uses_pinned_public_weights_without_credentials(self):
        with patch('transformers.AutoTokenizer.from_pretrained') as tokenizer, \
             patch('transformers.AutoModelForSeq2SeqLM.from_pretrained') as model:
            result = load_fresh_base('google-t5/t5-small', 'a'*40)
        for loader in [tokenizer,model]:
            loader.assert_called_once_with('google-t5/t5-small', revision='a'*40,
                                           token=False,trust_remote_code=False)
        self.assertEqual(result,(tokenizer.return_value,model.return_value))

    def test_fixed_grouped_nested_and_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.json'
            records = [dict(input=f'Show.{i}.S{j:02d}E01', target=f'Show {i} S{j:02d}E01')
                       for i in range(30) for j in [1,2]]
            source.write_text(json.dumps(records+[records[0]]))
            def run(output):
                return prepare(source, output, 42, .15, .15, [10,20,100])
            manifest = run(root/'one')
            self.assertEqual(manifest, run(root/'two'))
            self.assertEqual(manifest['duplicate_inputs_removed'], 1)
            groups = list(map(set, manifest['groups'].values()))
            self.assertFalse(groups[0]&groups[1] or groups[0]&groups[2] or groups[1]&groups[2])
            small = (root/'one/train_10.jsonl').read_text().splitlines()
            large = (root/'one/train_20.jsonl').read_text().splitlines()
            self.assertEqual(small, large[:10])
            for name, sha in manifest['files'].items():
                self.assertEqual(digest(root/'one'/name),sha)
            with self.assertRaisesRegex(ValueError,'already exists'):
                run(root/'one')

    def test_conflicting_inputs_fail_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.jsonl'
            source.write_text('\n'.join(json.dumps(dict(input='same',target=target))
                                        for target in ['A','B']))
            with self.assertRaisesRegex(ValueError,'Conflicting'):
                prepare(source,root/'output',42,.15,.15,[10])
            self.assertFalse((root/'output').exists())
