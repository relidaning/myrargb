"""Offline concurrency, offset and daily measurement regression checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from utils.kafka_utils import ConsumerUtil
from utils.resource_pool import ResourcePool
from utils.job_metrics import DailyFileHandler
from utils.settings import load_settings


class ScalingTests(unittest.TestCase):
    def test_single_partition_overlaps_but_commits_in_order(self):
        barrier = threading.Barrier(3)
        first_release = threading.Event()
        later_finished = threading.Event()
        messages = [Mock() for _ in range(3)]
        for index, message in enumerate(messages):
            message.error.return_value = None
            message.value.return_value = str(index).encode()
            message.partition.return_value = 2
            message.offset.return_value = index
        broker = Mock()
        broker.consume.side_effect = [messages, KeyboardInterrupt()]
        def job(data):
            barrier.wait(timeout=3)
            if data == '0':
                self.assertTrue(first_release.wait(3))
            else:
                later_finished.set()
        def release():
            self.assertTrue(later_finished.wait(3))
            broker.commit.assert_not_called()
            first_release.set()
        thread = threading.Thread(target=release)
        with patch('utils.kafka_utils.Consumer', return_value=broker), patch('utils.kafka_utils.record_job') as metrics:
            thread.start()
            ConsumerUtil().spawn('fixture', ['fixture'], job, workers=3)
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual([call.kwargs['message'] for call in broker.commit.call_args_list], messages)
        self.assertTrue(all(call.kwargs['asynchronous'] is False for call in broker.commit.call_args_list))
        self.assertEqual(metrics.call_count, 3)
        broker.close.assert_called_once()

    def test_failed_callback_cannot_commit_later_offsets(self):
        messages = [Mock(), Mock()]
        for index, message in enumerate(messages):
            message.error.return_value = None
            message.value.return_value = str(index).encode()
        broker = Mock()
        broker.consume.return_value = messages
        def job(data):
            if data == '0':
                raise RuntimeError('fixture failure')
        with patch('utils.kafka_utils.Consumer', return_value=broker), patch('utils.kafka_utils.record_job') as metrics:
            with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                ConsumerUtil().spawn('fixture', ['fixture'], job, workers=2)
        broker.commit.assert_not_called()
        broker.close.assert_called_once()
        self.assertEqual(metrics.call_count, 2)
        self.assertIn('failure', [call.args[1] for call in metrics.call_args_list])

    def test_pool_bounds_exclusive_resources_and_returns_after_error(self):
        factory = Mock(side_effect=object)
        closed = Mock()
        pool = ResourcePool(2, factory, closed)
        barrier = threading.Barrier(2)
        active = set()
        lock = threading.Lock()
        def job(_):
            with pool:
                resource = pool.get()
                self.assertIs(resource, pool.get())
                with lock:
                    self.assertNotIn(resource, active)
                    active.add(resource)
                barrier.wait(timeout=3)
                with lock:
                    active.remove(resource)
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(job, range(6)))
        self.assertEqual(factory.call_count, 2)
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            with pool:
                raise RuntimeError('fixture')
        with pool:
            self.assertIsNotNone(pool.get())
        pool.close()
        self.assertEqual(closed.call_count, 2)

    def test_settings_reject_invalid_worker_counts(self):
        self.assertEqual(load_settings({}).imdb_workers, 3)
        self.assertEqual(load_settings({'MYRARGB_IMDB_WORKERS': '5'}).imdb_workers, 5)
        for value in ('0', '-1', '1.5', 'nan', 'true'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'imdb_workers'):
                load_settings({'MYRARGB_IMDB_WORKERS': value})

    def test_daily_logging_append_and_midnight_rollover(self):
        import logging
        with tempfile.TemporaryDirectory() as directory:
            first = str(Path(directory) / '2026-11-12.log')
            second = str(Path(directory) / '2026-11-13.log')
            with patch.object(DailyFileHandler, '_filename', return_value=first):
                handler = DailyFileHandler(Path(directory) / '%Y-%m-%d.log')
            record = logging.makeLogRecord({'msg': 'fixture', 'levelno': logging.INFO})
            with patch.object(handler, '_filename', side_effect=[first, first, second]):
                handler.handle(record)
                handler.handle(record)
                handler.handle(record)
            handler.close()
            self.assertEqual(Path(first).read_text().count('fixture'), 2)
            self.assertEqual(Path(second).read_text().count('fixture'), 1)

    def test_report_known_outcomes_retries_midnight_and_empty_data(self):
        spec = importlib.util.spec_from_file_location('consumer_report', ROOT / 'scripts/consumer_report.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        start = datetime(2026, 11, 12, 23, tzinfo=timezone.utc)
        end = start + timedelta(hours=2)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f'{day}.log' for day in (12, 13)]
            for i, path in enumerate(paths):
                events = [{'timestamp': (start + timedelta(hours=i)).isoformat(), 'group': 'fixture',
                           'outcome': outcome, 'duration_seconds': duration}
                          for outcome, duration in [('success', 2), ('failure', 4)]]
                path.write_text('\n'.join('job_completed ' + json.dumps(event) for event in events)
                                + '\njob_completed broken\n', encoding='utf-8')
            report = module.render(paths, 'fixture', start, end)
            self.assertEqual(report.count('<td>50.0%</td>'), 2)
            self.assertEqual(report.count('<td>3.00</td>'), 2)
            self.assertIn('Malformed events: 2', report)
            self.assertIn('No completed jobs recorded', module.render([], 'fixture', start, end))
            self.assertIn('Scan limit reached: True', module.render(paths, 'fixture', start, end, max_bytes=1))


if __name__ == '__main__':
    unittest.main()
