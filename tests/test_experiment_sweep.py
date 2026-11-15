import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from model.experiment_sweep import (build_plan,successful_report,toml_text,render_comparison,
                                   seed_statistics,render_seed_comparison)


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.base=dict(batch_size=2,learning_rate=.0001,task_prefix='clean the title: ')
        self.grid=dict(training_size=720,epochs=5,training_seed=42,
                       learning_rates=[.00005,.0001,.0003],effective_batch_sizes=[8,16,32])

    def test_plan_and_roundtrip(self):
        plan=build_plan(self.base,self.grid,'hash')
        self.assertEqual(len(plan['trials']),9)
        self.assertEqual(plan,build_plan(self.base,self.grid,'hash'))
        self.assertNotEqual(plan['sweep_id'],build_plan(self.base,self.grid,'newhash')['sweep_id'])
        for trial in plan['trials']:
            config=trial['configuration']
            self.assertEqual(tomllib.loads(toml_text(config)),config)
            self.assertEqual(config['batch_size']*config['gradient_accumulation_steps'],trial['effective_batch_size'])
        self.assertNotIn('sweep_id',self.base)
        for rates,batches in [([float('nan')],[8]),([.1],[3]),([.1,.1],[8])]:
            with self.assertRaises(ValueError):
                build_plan(self.base,{**self.grid,'learning_rates':rates,'effective_batch_sizes':batches},'hash')

    def test_recovery_checks_identity_and_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'runs/one/report.json'
            path.parent.mkdir(parents=True)
            report=dict(status='success',configuration=self.base,manifest_sha256='hash',mode='train')
            path.write_text(json.dumps(report))
            self.assertEqual(successful_report(root,self.base,'hash'),report)
            self.assertIsNone(successful_report(root,self.base,'other'))
            path.write_text(json.dumps({**report,'status':'failure'}))
            self.assertIsNone(successful_report(root,self.base,'hash'))

    def test_comparison_uses_matching_logged_results_and_missing_states(self):
        plan=build_plan(self.base,self.grid,'hash')
        config=dict(sweep_id=plan['sweep_id'],trial_id=plan['trials'][0]['id'])
        event=dict(phase='sweep_trial_completed',metrics=dict(eval_title_match=.8),config=config)
        html=render_comparison(plan,[event])
        self.assertIn('80.0%',html)
        self.assertIn('pending',html)
        self.assertNotIn('80.0%',render_comparison(plan,[{**event,'config':{**config,'sweep_id':'other'}}]))

    def test_seed_plan_is_paired_settings_not_a_full_cross_product(self):
        grid={**self.grid,'training_seeds':[42,123,2026],
              'settings':[dict(learning_rate=.00005,effective_batch_size=8),
                          dict(learning_rate=.0001,effective_batch_size=16),
                          dict(learning_rate=.0001,effective_batch_size=32)]}
        plan=build_plan(self.base,grid,'hash')
        self.assertEqual(len(plan['trials']),9)
        self.assertEqual(plan['kind'],'seed_confirmation')
        self.assertEqual([t['configuration']['training_seed'] for t in plan['trials'][:3]],[42,123,2026])
        self.assertEqual(len({t['id'] for t in plan['trials']}),9)
        for seed in [-1,True,2**32]:
            with self.assertRaises(ValueError):
                build_plan(self.base,{**grid,'training_seeds':[seed]},'hash')
        events=[]
        for trial,score in zip(plan['trials'][:3],[.8,.9,1.]):
            events.append(dict(phase='sweep_trial_completed',metrics=dict(eval_title_match=score,eval_exact_match=score),
                               config=dict(sweep_id=plan['sweep_id'],trial_id=trial['id'],run_id=trial['id'])))
        # Duplicate log events must not count a repetition twice.
        events.append(events[0])
        stats=seed_statistics(plan,events)
        self.assertEqual(stats[0]['completed'],3)
        self.assertAlmostEqual(stats[0]['mean'],.9)
        self.assertAlmostEqual(stats[0]['sample_sd'],.1)
        self.assertIsNone(stats[1]['mean'])
        self.assertIsNone(seed_statistics(plan,events[:1])[0]['sample_sd'])
        html=render_seed_comparison(plan,events)
        self.assertIn('90.0%',html)
        self.assertIn('10.00 pp',html)
        self.assertIn('pending',html)

    def test_reuse_ignores_only_tracking_metadata_and_requires_fresh_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'runs/one/report.json'
            path.parent.mkdir(parents=True)
            config={**self.base,'training_seed':42,'sweep_id':'old','trial_id':'old'}
            report=dict(status='success',mode='train',configuration=config,manifest_sha256='hash',
                        initialization='fresh_public_pretrained_weights',resumed_checkpoint=None)
            path.write_text(json.dumps(report))
            target={**config,'sweep_id':'new','trial_id':'new'}
            self.assertIsNone(successful_report(root,target,'hash'))
            self.assertIsNotNone(successful_report(root,target,'hash',ignore_tracking=True))
            self.assertIsNone(successful_report(root,{**target,'training_seed':123},'hash',ignore_tracking=True))
            self.assertIsNone(successful_report(root,{**target,'learning_rate':.5},'hash',ignore_tracking=True))
            path.write_text(json.dumps({**report,'initialization':'existing_finetuned_model'}))
            self.assertIsNone(successful_report(root,target,'hash',ignore_tracking=True))
