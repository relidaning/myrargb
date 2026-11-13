"""Session ownership, cookie reuse, and local debugger routing regressions."""

import unittest
import test_crawler
from utils.settings import load_settings


class BrowserSessionTests(unittest.TestCase):
    def test_session_settings_reject_remote_debugging(self):
        for name, value in [('browser_mode', 'unknown'),
                            ('browser_session_display', 'unknown'),
                            ('browser_cdp_url', 'http://public.example:9222'),
                            ('browser_cdp_url', 'http://user:secret@localhost:9222'),
                            ('browser_cdp_url', 'http://localhost:9222/?token=secret')]:
            with self.assertRaisesRegex(ValueError, name) as error:
                load_settings({'MYRARGB_' + name.upper(): value})
            self.assertNotIn('secret', str(error.exception))

    def test_real_browser_reuses_verified_context_and_survives_disconnect(self):
        test_crawler.IsolatedCrawlerTests().run_isolated('''
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
import os
import socket
import subprocess
import time
from playwright.sync_api import sync_playwright
from browserdriver.driver import PlaywrightDriver
from browserdriver.session import debugger_websocket
from utils.settings import settings

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        verified = self.path == '/manual' or 'verified=yes' in self.headers.get('Cookie', '')
        self.send_response(200 if verified else 403)
        if self.path == '/manual':
            self.send_header('Set-Cookie', 'verified=yes; Path=/')
        self.end_headers()
        self.wfile.write(('<table class="lista2t"><tr><th>Catalog</th></tr></table>'+self.path).encode() if verified else b'<form id="challenge-form"></form>')
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f'http://127.0.0.1:{server.server_port}'
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
endpoint = f'http://127.0.0.1:{port}'
with sync_playwright() as playwright:
    executable = playwright.chromium.executable_path
profile = settings.database_path.parent / 'chrome-profile'
process = subprocess.Popen([executable, '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
                            '--disable-background-networking', '--disable-component-update', '--no-first-run',
                            f'--user-data-dir={profile}', f'--remote-debugging-port={port}', base + '/manual'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
driver = None
try:
    deadline = time.monotonic() + 10
    while True:
        try:
            debugger_websocket(endpoint, 1)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.1)
    config = replace(settings, browser_mode='session', browser_cdp_url=endpoint)
    # This deliberately broken global proxy must never intercept local debugger discovery.
    with patch.dict(os.environ, {'HTTP_PROXY':'http://127.0.0.1:1', 'http_proxy':'http://127.0.0.1:1',
                                 'HTTPS_PROXY':'http://127.0.0.1:1', 'https_proxy':'http://127.0.0.1:1',
                                 'NO_PROXY':'', 'no_proxy':''}), patch('browserdriver.driver.settings', config):
        driver = PlaywrightDriver()
        # Wait for the browser's original manual page to set the session cookie.
        driver._executor.submit(lambda: driver._context.pages[0].wait_for_load_state('load')).result()
        assert 'lista2t' in driver.fetch(base + '/protected')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        original_pages = driver._executor.submit(lambda: len(driver._context.pages)).result()
        other = PlaywrightDriver()
        try:
            barrier = Barrier(2)
            first_new_page, second_new_page = driver._new_page, other._new_page
            def first():
                barrier.wait(timeout=5)
                return first_new_page()
            def second():
                barrier.wait(timeout=5)
                return second_new_page()
            driver._new_page, other._new_page = first, second
            with ThreadPoolExecutor(max_workers=2) as pool:
                for i in range(4):
                    left = pool.submit(driver.fetch, base + f'/catalog-{i}')
                    right = pool.submit(other.fetch, base + f'/imdb-{i}')
                    assert f'/catalog-{i}' in left.result(timeout=10)
                    assert f'/imdb-{i}' in right.result(timeout=10)
            driver._new_page = first_new_page
        finally:
            other.close()
        assert original_pages >= 1
        driver.close()
        driver = None
        assert process.poll() is None
        assert debugger_websocket(endpoint, 2)
        driver = PlaywrightDriver()
        assert 'lista2t' in driver.fetch(base + '/protected')
        assert driver._executor.submit(lambda: len(driver._context.pages)).result() == original_pages
        driver.close()
        driver = None
        assert process.poll() is None
finally:
    if driver:
        driver.close()
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    server.shutdown()
    server.server_close()
    thread.join()
''')

    def test_launcher_uses_dedicated_profile_and_rejects_proxy_credentials(self):
        test_crawler.IsolatedCrawlerTests().run_isolated('''
from dataclasses import replace
import importlib.util
from unittest.mock import patch
from utils.settings import settings, PROJECT_ROOT
spec = importlib.util.spec_from_file_location('browser_launcher', PROJECT_ROOT / 'scripts/open_crawler_browser.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with patch.object(module.shutil, 'which', return_value='/fixture/chrome'):
    args = module.launch_arguments()
    assert f'--user-data-dir={settings.browser_profile_path}' in args
    assert '--remote-debugging-address=127.0.0.1' in args
    assert '--no-proxy-server' in args
    headless = module.launch_arguments('headless')
    assert '--headless=new' in headless
    assert '--ozone-platform=x11' not in headless
    background = module.launch_arguments('background')
    assert '--ozone-platform=headless' in background
    assert '--headless=new' not in background
    assert '--ozone-platform=x11' not in background
    with patch.object(module, 'settings', replace(settings, browser_proxy='http://user:secret@localhost:1234')):
        try:
            module.launch_arguments()
            raise AssertionError('Credentials must not enter process arguments')
        except ValueError as error:
            assert 'secret' not in str(error)
''')


if __name__ == '__main__':
    unittest.main()
