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
        self.assertEqual(defaults.log_path, PROJECT_ROOT / 'logs/%Y-%m-%d.log')
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

    def test_dotenv_is_not_loaded(self):
        import shutil
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            shutil.copy2(PROJECT_ROOT / 'config/defaults.toml', root / 'config/defaults.toml')
            (root / '.env').write_text('MYRARGB_PORT=1234\nMYRARGB_IMDB_WORKERS=99\n')
            with patch('utils.settings.PROJECT_ROOT', root), patch.dict(os.environ, {}, clear=True):
                settings = load_settings()
            self.assertEqual(settings.port, 5000)
            self.assertEqual(settings.imdb_workers, 3)

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
                       MYRARGB_BROWSER_MODE='managed', MYRARGB_BACKGROUND_JOBS='false',
                       MYRARGB_MODEL_PATH=f'{directory}/missing-model',
                       MYRARGB_TRAINING_SPLIT_PATH=f'{directory}/training/split.json',
                       MYRARGB_TRAINING_OUTPUT_PATH=f'{directory}/training/runs',
                       MYRARGB_TRAINING_LOG_PATH=f'{directory}/training/logs')
            result = subprocess.run([str(PROJECT_ROOT / '.venv/bin/python'), '-c', code],
                                    cwd=directory, env=env, text=True, capture_output=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_rebuild_bloom_uses_shared_keys_without_changing_titles(self):
        self.run_isolated('''
import subprocess
import sys
import sqlite3
from utils.settings import PROJECT_ROOT, settings
from db.repository import MovieRepository
from db_model import Movie
from utils.bloom_utils import BloomUtils
repo = MovieRepository()
repo.insert(Movie(title='The.Matrix', title_accurate='THE MATRIX'))
repo.insert(Movie(title='Amélie.'))
BloomUtils().add('legacy title')
result = subprocess.run([sys.executable, str(PROJECT_ROOT / 'scripts/rebuild_bloom.py')], capture_output=True, text=True)
assert result.returncode == 0, result.stderr
assert '2 normalized title keys' in result.stdout, result.stdout
bloom = BloomUtils()
for title in ['thematrix', 'The Matrix', 'THE.MATRIX', 'amélie']:
    assert bloom.hasItem(title), title
assert repo.find_one(1).title == 'The.Matrix'
assert list((settings.database_path.parent / 'backups').glob('bloom-*.bin'))
''')

    def test_manual_rematch_and_duplicate_controls(self):
        self.run_isolated('''
from unittest.mock import patch
import app
from crawler import ImdbCrawler
from db_model import Movie
from browserdriver.errors import BrowserFetchError
repo = app.movieRepository
one = repo.insert(Movie(filename='Example.2020.1080p', title='Example', year='2020', score='8', poster='old', summary='old'))
two = repo.insert(Movie(filename='Example.2020.720p', title='Example', year='2020'))
client = app.app.test_client()
assert b'Mismatch' in client.get('/').data
with patch('crawler.DriverFactory.create_driver') as factory:
    crawler = ImdbCrawler()
    def candidate(title='Ex.am ple', year='2020', kind=''):
        return f'<li class="ipc-metadata-list-summary-item"><a href="/title/tt123/"><h3 class="ipc-title__text">{title}</h3></a><li class="ipc-inline-list__item">{year}</li>{kind}<span class="ipc-rating-star--rating">7</span></li>'
    def html(*rows):
        return '<ul class="ipc-metadata-list--base">' + ''.join(rows) + '</ul>'
    factory.return_value.fetch.return_value = html(candidate())
    with patch.object(crawler, '_fetch_summary', return_value=None), patch('db.service._get_imdb_crawler', return_value=crawler):
        assert client.post(f'/items/{one}/mismatch').status_code == 200
    assert factory.return_value.fetch.call_args.args[0] == 'https://www.imdb.com/find/?q=example&s=tt&exact=true&ref_=fn_ttl_ex'
    assert repo.find_one(one).poster is None and repo.find_one(one).summary is None
    for rows in [(candidate('Different'),), (candidate(year='2021'),), (candidate(kind='TV Series'),), (candidate(), candidate())]:
        factory.return_value.fetch.return_value = html(*rows)
        with patch('db.service._get_imdb_crawler', return_value=crawler):
            assert client.post(f'/items/{one}/mismatch').status_code == 422
    factory.return_value.fetch.side_effect = BrowserFetchError('Unavailable')
    with patch('db.service._get_imdb_crawler', return_value=crawler):
        assert client.post(f'/items/{one}/mismatch').status_code == 503
    from browserdriver.errors import UnexpectedPageError
    factory.return_value.fetch.side_effect = UnexpectedPageError('Changed layout')
    with patch('db.service._get_imdb_crawler', return_value=crawler):
        assert client.post(f'/items/{one}/mismatch').status_code == 502
assert client.delete(f'/items/{two}/duplicate').status_code == 200
assert repo.find_one(one).id == one
assert client.delete(f'/items/{one}/duplicate').status_code == 200
assert repo.count() == 0
assert client.delete('/items/999999/duplicate').status_code == 404
from pathlib import Path
from utils.settings import PROJECT_ROOT
with patch('crawler.DriverFactory.create_driver') as factory:
    crawler = ImdbCrawler()
    factory.return_value.fetch.return_value = (PROJECT_ROOT / 'tests/fixtures/imdb-brotherhood.html').read_text()
    with patch.object(crawler, '_fetch_summary', return_value=None):
        result = crawler.crawl(Movie(id=6763, title_accurate='Brotherhood', year='2021', media_type='movie'), exact=True)
    assert result.title == 'Brotherhood' and result.score == '6.5', result
    factory.return_value.fetch.return_value = '<h1>Changed layout</h1>'
    try:
        crawler.crawl(Movie(id=6763, title='Brotherhood'), exact=True)
        raise AssertionError('Parse failures must not become unmatched')
    except BrowserFetchError:
        pass

''')

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

    def test_imdb_service_crawls_and_persists_three_rows_concurrently(self):
        self.run_isolated('''
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock, patch
from db.service import MovieService, _imdb_crawler_slots
from db_model import Movie
service = MovieService()
ids = [service.movieRepository.insert(Movie(title=f'Example {i}', filename=f'Example.{i}.2020')) for i in range(3)]
barrier = Barrier(3)
def crawl(movie):
    barrier.wait(timeout=3)
    return movie.model_copy(update={'score': '8.0'})
def factory():
    crawler = Mock()
    crawler.crawl.side_effect = crawl
    return crawler
with patch.object(_imdb_crawler_slots, '_factory', side_effect=factory) as constructor:
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(service.crawl_imdb, [Movie(id=id) for id in ids]))
    assert constructor.call_count == 3
assert all(service.movieRepository.find_one(id).score == '8.0' for id in ids)
_imdb_crawler_slots.close()
''')

    def test_imdb_startup_configures_parallel_callbacks(self):
        self.run_isolated('''
import os
os.environ['MYRARGB_BACKGROUND_JOBS'] = 'true'
os.environ['MYRARGB_IMDB_WORKERS'] = '3'
import app
from unittest.mock import patch
with patch('app.Thread') as thread, patch.object(app.app, 'run'):
    app.main()
    imdb = [call for call in thread.call_args_list
            if call.kwargs.get('kwargs', {}).get('group_id', '').endswith('.crawl_imdb')]
    assert len(imdb) == 1
    assert imdb[0].kwargs['kwargs']['workers'] == 3
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
