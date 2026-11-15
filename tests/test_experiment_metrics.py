from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from model.experiment_metrics import record_event,load_events,render


class ExperimentMetricsTests(unittest.TestCase):
    def test_append_rollover_malformed_and_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('model.experiment_metrics.datetime') as clock:
                clock.now.return_value = datetime(2026,1,1,23,59,tzinfo=timezone.utc)
                record_event('r','baseline',{'baseline_title_match':.1},root=root)
                record_event('r','started',{},root=root)
                clock.now.return_value = datetime(2026,1,2,tzinfo=timezone.utc)
                record_event('r','progress',{'epoch':1,'eval_title_match':.5},root=root)
            paths=sorted((root/'logs').glob('*.log'))
            self.assertEqual(len(paths),2)
            with paths[-1].open('a') as output:output.write('training_event invalid\n')
            events,bad,limited=load_events(paths)
            self.assertEqual(len(events),3)
            self.assertEqual(bad,1)
            self.assertFalse(limited)
            self.assertTrue(load_events(paths,1)[2])
            html=render(events,bad)
            self.assertIn('epoch 0, value 0.1',html)
            self.assertIn('epoch 1, value 0.5',html)
            self.assertIn('0.1000',html)

    def test_empty_and_escaped(self):
        self.assertIn('No training events recorded',render([]))
        html=render([dict(timestamp='2026-01-01',run='<script>',phase='failed',metrics={},config={})])
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('unavailable',html)

    def test_restored_checkpoint_evaluation_does_not_replace_epoch_curve(self):
        def event(value):
            return dict(timestamp='2026-01-01',run='r',phase='progress',
                        metrics=dict(epoch=5,eval_title_match=value),config={})
        html=render([event(.9),event(.8)])
        self.assertIn('epoch 5, value 0.9',html)
        self.assertNotIn('epoch 5, value 0.8',html)
