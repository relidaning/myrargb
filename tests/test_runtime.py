"""Offline regression checks. All runtime imports use disposable storage."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.settings import PROJECT_ROOT, load_settings


class SettingsTests(unittest.TestCase):
    def test_precedence_and_relative_paths(self):
        defaults = load_settings({})
        self.assertEqual(defaults.log_path, PROJECT_ROOT / 'logs/app.log')
        self.assertEqual(defaults.training_log_path, PROJECT_ROOT / 'logs/training')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'custom.toml'
            config.write_text('port = 7000\ndebug = true\ndatabase_path = "data/custom.db"\n')
            settings = load_settings({'MYRARGB_CONFIG': str(config), 'MYRARGB_PORT': '8000',
                                      'DEBUG': 'false', 'MYRARGB_DEBUG': 'true'})
            self.assertEqual(settings.port, 8000)
            self.assertTrue(settings.debug)
            self.assertEqual(settings.database_path, PROJECT_ROOT / 'data/custom.db')
            self.assertFalse(load_settings({'DEBUG': 'false'}).debug)
            self.assertEqual(load_settings({'LOGGER_LEVEL': 'warning'}).log_level, 'WARNING')

    def test_invalid_values_name_setting_without_echoing_value(self):
        for name, value in [('port', '65536'), ('port', '0'), ('port', 'secret'),
                            ('debug', 'secret'), ('log_level', 'secret'),
                            ('tvmaze_timeout', 'nan'), ('tvmaze_timeout', 'inf'),
                            ('tvmaze_days_back', '-1'), ('database_path', '')]:
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(ValueError, name) as error:
                    load_settings({f'MYRARGB_{name.upper()}': value})
                self.assertNotIn('secret', str(error.exception))

    def test_unknown_file_setting_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'custom.toml'
            config.write_text('prot = 5000\n')
            with self.assertRaisesRegex(ValueError, 'prot'):
                load_settings({'MYRARGB_CONFIG': str(config)})
            config.write_text('port = 1.5\n')
            with self.assertRaisesRegex(ValueError, 'port'):
                load_settings({'MYRARGB_CONFIG': str(config)})


class RuntimeTests(unittest.TestCase):
    def run_isolated(self, code):
        with tempfile.TemporaryDirectory() as directory:
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith('MYRARGB_')}
            env.update(PYTHONPATH=str(PROJECT_ROOT / "src"),
                       MYRARGB_DATABASE_PATH=f'{directory}/db/catalog.db',
                       MYRARGB_BLOOM_PATH=f'{directory}/bloom/filter.bin',
                       MYRARGB_LOG_PATH=f'{directory}/logs/app.log',
                       MYRARGB_BACKGROUND_JOBS='false',
                       MYRARGB_MODEL_PATH=f'{directory}/missing-model')
            result = subprocess.run([str(PROJECT_ROOT / '.venv/bin/python'), '-c', code],
                                    cwd=directory, env=env, text=True, capture_output=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_browse_from_another_directory_without_ml_or_network(self):
        result = self.run_isolated('''
import socket
import sys
import time

def forbidden(*args, **kwargs):
    raise AssertionError('Network access during browsing')
socket.socket.connect = forbidden
socket.create_connection = forbidden
start = time.perf_counter()
import app
from db.db import db
from db_model import Movie
from model.model import model
from utils.settings import settings
from unittest.mock import patch
from utils.kafka_utils import ProducerUtil
assert model._instance is None
assert 'torch' not in sys.modules
assert 'transformers' not in sys.modules
assert 'datasets' not in sys.modules
app.movieRepository.insert(Movie(title='Sample movie', filename='Sample.2020', score='8.1'))
client = app.app.test_client()
with patch('utils.kafka_utils.Producer', side_effect=AssertionError('Broker initialized')):
    for url in ('/', '/?sort=seed', '/?q=Sample', '/tv', '/static/css/style.css', '/static/images/default.jpg'):
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code)
    assert b'Sample movie' in client.get('/').data
assert model._instance is None
assert settings.database_path.exists()
assert settings.bloom_path.exists()
assert settings.log_path.exists()
db.conn.close()
print('Offline UI smoke passed in %.3fs' % (time.perf_counter() - start))
''')
        print(result.strip())

    def test_entrypoint_applies_settings_without_starting_background_jobs(self):
        self.run_isolated('''
import os
import runpy
from unittest.mock import patch
from utils.settings import PROJECT_ROOT, settings
assert settings.background_jobs is False
with patch('threading.Thread.start', side_effect=AssertionError('Background job started')):
    with patch('flask.Flask.run') as run:
        runpy.run_path(str(PROJECT_ROOT / 'app.py'), run_name='__main__')
        run.assert_called_once_with(host=settings.host, port=settings.port,
                                    debug=settings.debug, use_reloader=False)
''')

    def test_manual_crawl_script_uses_configured_endpoint_from_another_directory(self):
        self.run_isolated('''
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import subprocess
from utils.settings import PROJECT_ROOT

paths = []
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        paths.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'fixture-response')
    def log_message(self, *args):
        pass

server = HTTPServer(('127.0.0.1', 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    env = dict(os.environ, MYRARGB_HOST='127.0.0.1',
               MYRARGB_PORT=str(server.server_port), NO_PROXY='127.0.0.1')
    result = subprocess.run([str(PROJECT_ROOT / 'scripts/send_msg_to_kafka.sh')],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'fixture-response'
    assert paths == ['/crawl_rargb?incremental=true']
finally:
    server.shutdown()
    server.server_close()
    thread.join()
''')

    def test_lazy_model_initializes_once_under_concurrency(self):
        self.run_isolated('''
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from model.model import LazyModel
model = LazyModel()
instance = Mock()
with patch('model.model.MyRargbModel', return_value=instance) as constructor:
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(model.predict, range(20)))
    model.train([])
    constructor.assert_called_once_with()
    assert instance.predict.call_count == 20
    instance.train.assert_called_once_with([])
''')

    def test_kafka_probe_recovers_and_producer_is_lazy(self):
        self.run_isolated('''
from unittest.mock import Mock, patch
from confluent_kafka import KafkaException
from utils.kafka_utils import ProducerUtil
from utils.settings import settings
broker = Mock()
broker.list_topics.side_effect = [KafkaException(), None]
with patch('utils.kafka_utils.Producer', return_value=broker) as constructor:
    producer = ProducerUtil()
    constructor.assert_not_called()
    assert producer.available() is False
    assert producer.available() is False
    assert broker.list_topics.call_count == 1
    producer._checked_at -= settings.kafka_probe_cache_seconds + 1
    assert producer.available() is True
    constructor.assert_called_once_with({'bootstrap.servers': settings.kafka_bootstrap_servers})
    producer.produce('test-topic', {'test': True})
    broker.produce.assert_called_once()
''')


if __name__ == '__main__':
    unittest.main()
