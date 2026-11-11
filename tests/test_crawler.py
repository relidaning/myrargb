"""Offline crawler tests: disposable DB plus real Chromium on a loopback fixture."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from browserdriver.errors import challenge_reason
from utils.settings import load_settings


class ChallengeTests(unittest.TestCase):
    def test_detection_does_not_reject_normal_cloudflare_pages(self):
        self.assertEqual(challenge_reason('<h1>Loading</h1>', {'cf-mitigated': 'challenge'}),
                         'Cloudflare verification')
        self.assertEqual(challenge_reason('<script>window._cf_chl_opt = {}</script>'),
                         'Cloudflare verification')
        self.assertEqual(challenge_reason('<form id="challenge-form"></form>'),
                         'Cloudflare verification')
        self.assertIsNone(challenge_reason('<table class="lista2t"></table>'
                                          '<script src="/cdn-cgi/challenge-platform/jsd.js"></script>',
                                          {'cf-ray': 'abc-XYZ'}))

    def test_new_configuration_and_invalid_proxy_are_safe(self):
        settings = load_settings({'MYRARGB_BROWSER_PROXY': 'http://user:secret@localhost:8080',
                                  'MYRARGB_BROWSER_CHALLENGE_WAIT': '0',
                                  'MYRARGB_BROWSER_HEADLESS': 'false'})
        self.assertFalse(settings.browser_headless)
        self.assertEqual(settings.browser_challenge_wait, 0)
        self.assertEqual(settings.browser_diagnostics_path, ROOT / 'logs/browser')
        self.assertNotIn('secret', repr(settings))
        for proxy in ('secret', 'ftp://user:secret@localhost', 'http://user:secret@localhost:wrong',
                      'http://localhost/path', 'http://localhost:99999'):
            with self.assertRaisesRegex(ValueError, 'browser_proxy') as error:
                load_settings({'MYRARGB_BROWSER_PROXY': proxy})
            self.assertNotIn('secret', str(error.exception))


class IsolatedCrawlerTests(unittest.TestCase):
    def run_isolated(self, code):
        with tempfile.TemporaryDirectory() as directory:
            env = {key: value for key, value in os.environ.items() if not key.startswith('MYRARGB_')}
            env.update(PYTHONPATH=str(ROOT / 'src'),
                       MYRARGB_DATABASE_PATH=f'{directory}/catalog.db',
                       MYRARGB_BLOOM_PATH=f'{directory}/bf.bin',
                       MYRARGB_LOG_PATH=f'{directory}/app.log',
                       MYRARGB_BROWSER_DIAGNOSTICS_PATH=f'{directory}/browser',
                       MYRARGB_BROWSER_PROXY='', MYRARGB_BROWSER_HEADLESS='true',
                       MYRARGB_BROWSER_CHALLENGE_WAIT='0.2', MYRARGB_BACKGROUND_JOBS='false')
            result = subprocess.run([str(ROOT / '.venv/bin/python'), '-c', code], cwd=directory,
                                    env=env, text=True, capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_browser_diagnostics_and_bounded_passive_wait(self):
        self.run_isolated('''
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit
from unittest.mock import patch
from browserdriver.driver import PlaywrightDriver
from browserdriver.errors import BrowserChallengeError, BrowserFetchError
from utils.settings import settings, PROJECT_ROOT
import subprocess
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        self.send_response(403 if path == '/blocked' else 500 if path == '/error' else 200)
        self.send_header('Content-Type', 'text/html')
        if path == '/blocked':
            self.send_header('cf-mitigated', 'challenge')
            self.send_header('cf-ray', '123abc-TEST')
        self.end_headers()
        body = {
            '/blocked': '<title>Just a moment...</title><form id="challenge-form">Verify you are human</form>',
            '/error': '<title>Upstream unavailable</title>',
            '/normal': '<title>Catalog</title><table class="lista2t"><tr><th>File</th></tr></table>',
            '/transient': '<form id="challenge-form">Checking...</form><script>setTimeout(() => {document.body.innerHTML = "<table class=lista2t></table>"}, 50)</script>',
        }.get(path, '<title>Unknown</title>')
        self.wfile.write(body.encode())
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f'http://127.0.0.1:{server.server_port}'
driver = None
try:
    # Context routing never contacts external domains, even if a fixture changes.
    driver = PlaywrightDriver()
    driver._context.route('**/*', lambda route: route.continue_()
                          if urlsplit(route.request.url).hostname == '127.0.0.1' else route.abort())
    assert 'lista2t' in driver.fetch(base + '/normal')
    assert 'lista2t' in driver.fetch(base + '/transient')
    for path, kind in [('/blocked?token=do-not-store', BrowserChallengeError), ('/error', BrowserFetchError)]:
        try:
            driver.fetch(base + path)
            raise AssertionError('Expected browser failure')
        except kind as error:
            assert error.diagnostic_id
            data = json.loads((settings.browser_diagnostics_path / f'{error.diagnostic_id}.json').read_text())
            assert data['status'] in (403, 500)
            assert data['screenshot']
            assert (settings.browser_diagnostics_path / data['screenshot']).stat().st_size > 0
            assert 'do-not-store' not in json.dumps(data)
            if path.startswith('/blocked'):
                assert data['cf_ray'] == '123abc-TEST'
                assert data['title'] == 'Just a moment...'
    # Even a read-only/unwritable diagnostics destination preserves the original failure.
    settings.log_path.write_text('fixture')
    with patch('browserdriver.driver.settings') as configured:
        configured.browser_challenge_wait = 0
        configured.browser_navigation_timeout = 5
        configured.browser_diagnostics_path = settings.log_path  # Existing file, not directory.
        configured.browser_proxy = ''
        try:
            driver.fetch(base + '/blocked')
            raise AssertionError('Expected challenge')
        except BrowserChallengeError as error:
            assert error.diagnostic_id is None
    # Exercise the rendered UI error handler in Chromium with network requests blocked.
    import app
    page = driver._context.new_page()
    page.evaluate("window.axios = {get: (url) => url === '/tv/notifications' ? Promise.resolve({data:{notifications:[]}}) : Promise.reject({response:{data:{message:'Cloudflare verification blocked the crawl. Diagnostic: fixture.'}}})}")
    page.set_content(app.app.test_client().get('/').get_data(as_text=True), wait_until='domcontentloaded')
    page.evaluate('crawlrargb()')
    page.wait_for_function("document.getElementById('toast').textContent.includes('Diagnostic: fixture')")
    assert page.locator('#mask').evaluate("el => el.style.display") == 'none'
    assert page.locator('#toast').is_visible()
    page.close()
    driver.close()
    driver = None
    result = subprocess.run([sys.executable, str(PROJECT_ROOT / 'scripts/check_crawler.py'),
                             '--url', base + '/normal'], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Catalog accessible' in result.stdout
finally:
    if driver:
        driver.close()
    server.shutdown()
    server.server_close()
    thread.join()
''')

    def test_crawl_route_reports_failures_without_mutating_records(self):
        self.run_isolated('''
from unittest.mock import Mock, patch
import app
from browserdriver.errors import BrowserChallengeError, BrowserFetchError, UnexpectedPageError
from crawler import RargbCrawler, ImdbCrawler
from db.db import db
from db_model import Movie
from workflow import Workflow

client = app.app.test_client()
count = app.service.count_items(Workflow.NONE)
collected = app.service._get_collected_range()
for failure in [BrowserChallengeError('Cloudflare verification blocked the crawl.',
                                      diagnostic_id='fixture', ray_id='abc-TEST'),
                BrowserFetchError('Upstream returned HTTP 500.'),
                UnexpectedPageError('The catalog table is missing.'), RuntimeError('private-proxy-password')]:
    fake = Mock()
    fake.crawl.side_effect = failure
    with patch('db.service._producer.available', return_value=True), patch('db.service.RargbCrawler', return_value=fake):
        response = client.get('/crawl_rargb?incremental=true')
    assert response.status_code == 502, response.data
    assert response.json['status'] == 'error'
    assert 'private-proxy-password' not in str(response.json)
    fake.close.assert_called_once()
    assert app.service.count_items(Workflow.NONE) == count
    assert app.service._get_collected_range() == collected
    if isinstance(failure, BrowserChallengeError):
        assert response.json['code'] == 'browser_challenge'
        assert response.json['diagnostic_id'] == 'fixture'
        assert response.json['ray_id'] == 'abc-TEST'
with patch('db.service._producer.available', return_value=False):
    assert client.get('/crawl_rargb').status_code == 503
with patch('db.service._producer.available', return_value=True), patch('db.service.RargbCrawler') as factory:
    factory.return_value.crawl.return_value = []
    assert client.get('/crawl_rargb').status_code == 200

with patch('crawler.DriverFactory.create_driver') as factory:
    crawler = RargbCrawler()
    factory.return_value.fetch.return_value = '<form id="challenge-form"></form>'
    try:
        crawler.crawl({'page': 1})
        raise AssertionError('Expected challenge')
    except BrowserChallengeError:
        pass
    factory.return_value.fetch.return_value = '<h1>Changed layout</h1>'
    try:
        crawler.crawl({'page': 1})
        raise AssertionError('Expected parse error')
    except UnexpectedPageError:
        pass
    factory.return_value.fetch.return_value = '<table class="lista2t"><tr><th>File</th></tr></table>'
    assert crawler.crawl({'page': 1}) == []
    imdb = ImdbCrawler()
    factory.return_value.fetch.side_effect = BrowserChallengeError('Blocked')
    try:
        imdb.crawl(Movie(id=1, title='Example'))
        raise AssertionError('Challenge must not become unmatched')
    except BrowserChallengeError:
        pass
db.conn.close()
''')

    def test_proxy_is_passed_explicitly_without_logging_credentials(self):
        self.run_isolated('''
from dataclasses import replace
from unittest.mock import patch, Mock
from browserdriver.driver import PlaywrightDriver
from utils.settings import settings
with patch('playwright.sync_api.sync_playwright') as playwright, patch('browserdriver.driver.settings',
        replace(settings, browser_proxy='http://name:secret@127.0.0.1:8080')):
    driver = PlaywrightDriver()
    launch = playwright.return_value.start.return_value.chromium.launch
    options = launch.call_args.kwargs
    assert options['proxy'] == {'server': 'http://127.0.0.1:8080', 'username': 'name', 'password': 'secret'}
    assert 'secret' not in options['proxy']['server']
    driver.close()
''')


if __name__ == '__main__':
    unittest.main()
