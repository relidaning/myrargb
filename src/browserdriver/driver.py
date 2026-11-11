import time
import logging
from selenium import webdriver
from utils.settings import settings
from abc import ABC, abstractmethod
import undetected_chromedriver as uc
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit, unquote
from browserdriver.errors import BrowserFetchError, BrowserChallengeError, challenge_reason

logger = logging.getLogger(__name__)



class BrowserDriver(ABC):
    @abstractmethod
    def fetch(self, url: str) -> str: ...

    def close(self) -> None:
        """No-op by default; override if the driver owns a long-lived process."""


class SeleniumBrowerDriver(BrowserDriver):
    def __init__(self):
        options = webdriver.ChromeOptions()
        # MUST run with real UI, Cloudflare blocks headless
        # comment the next line if you want visible browser
        options.add_argument("--headless")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--window-size=1920,1080")
        # options.add_argument("--disable-dev-shm-usage")

        logger.info(" [v] Selenium WebDriver initialized.")

        self.driver = webdriver.Remote(
            command_executor=settings.chrome_url, options=options
        )
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def __del__(self):
        self.driver.quit()
        logger.info(" [x] Selenium WebDriver closed.")

    def fetch(self, url: str) -> str:
        self.driver.get(url)
        time.sleep(10)
        html = self.driver.page_source
        # logger.debug(f"Fetched HTML content: {html}")
        return html


class UndetectedChromeBrowserDriver(BrowserDriver):
    def __init__(self):
        self.driver = uc.Chrome(version_main=147)
        logger.info(" [v] Undetected Chrome WebDriver initialized.")

    def __del__(self):
        self.driver.quit()
        logger.info(" [x] Undetected Chrome WebDriver closed.")

    def fetch(self, url: str) -> str:
        self.driver.get(url)
        time.sleep(10)
        html = self.driver.page_source
        # logger.debug(f"Fetched HTML content: {html}")
        return html


class PlaywrightDriver(BrowserDriver):
    def __init__(self):
        # All sync Playwright objects must live on the thread that created them.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='playwright')
        self._closed = False
        try:
            self._executor.submit(self._launch).result()
        except Exception:
            self._executor.shutdown()
            raise

    def _launch(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            options = {
                "headless": settings.browser_headless,
                "args": ["--no-sandbox", "--disable-dev-shm-usage"],
            }
            if settings.browser_proxy:
                parsed = urlsplit(settings.browser_proxy)
                hostname = parsed.hostname
                if ':' in hostname:
                    hostname = f'[{hostname}]'
                server = f'{parsed.scheme}://{hostname}'
                if parsed.port:
                    server += f':{parsed.port}'
                proxy = {"server": server}
                if parsed.username is not None:
                    proxy['username'] = unquote(parsed.username)
                if parsed.password is not None:
                    proxy['password'] = unquote(parsed.password)
                options['proxy'] = proxy
            else:
                options['args'].append('--no-proxy-server')
            self._browser = self._pw.chromium.launch(**options)
            self._context = self._browser.new_context()
        except Exception:
            if hasattr(self, '_browser'):
                self._browser.close()
            self._pw.stop()
            raise BrowserFetchError('Browser startup failed; check browser installation, display and proxy settings.') from None
        logger.info('Browser route: %s', 'explicit proxy' if settings.browser_proxy else 'direct')

    def _diagnostics(self, page, url, response, reason):
        diagnostic_id = uuid4().hex
        directory = settings.browser_diagnostics_path
        metadata = {
            'id': diagnostic_id,
            'reason': reason,
            'requested_url': self._safe_url(url),
            'final_url': self._safe_url(page.url),
            'status': response.status if response else None,
            'proxy_mode': 'explicit' if settings.browser_proxy else 'direct',
            'title': None,
            'cf_ray': None,
            'screenshot': None,
        }
        if response:
            ray = response.headers.get('cf-ray')
            metadata['cf_ray'] = BrowserFetchError('', ray_id=ray).ray_id
        try:
            metadata['title'] = page.title()[:300]
        except Exception:
            pass
        try:
            directory.mkdir(parents=True, exist_ok=True)
            # Bound screenshot work even when the page never finishes loading.
            page.screenshot(path=str(directory / f'{diagnostic_id}.png'), timeout=3000)
            metadata['screenshot'] = f'{diagnostic_id}.png'
        except Exception:
            # Metadata remains useful even when screenshot capture fails.
            pass
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f'{diagnostic_id}.json').write_text(json.dumps(metadata, indent=2))
            logger.warning('Browser failure: %s; diagnostic=%s; status=%s; Ray ID=%s',
                           reason, diagnostic_id, metadata['status'], metadata['cf_ray'])
            return diagnostic_id, metadata['cf_ray']
        except OSError:
            logger.warning('Could not write browser diagnostics; check browser_diagnostics_path permissions.')
            return None, metadata['cf_ray']

    @staticmethod
    def _safe_url(url):
        # Diagnostic URLs exclude credentials, query parameters and fragments.
        parts = urlsplit(url)
        host = parts.hostname or ''
        if ':' in host:
            host = f'[{host}]'
        if parts.port:
            host += f':{parts.port}'
        return urlunsplit((parts.scheme, host, parts.path, '', ''))

    def fetch(self, url: str) -> str:
        return self._executor.submit(self._fetch, url).result()

    def _fetch(self, url: str) -> str:
        try:
            page = self._context.new_page()
        except Exception:
            # Recover once after a crashed/disconnected browser. Do not log raw
            # Playwright exceptions: their URLs may include proxy credentials.
            logger.warning('Browser unusable; relaunching once.')
            self._shutdown()
            self._launch()
            try:
                page = self._context.new_page()
            except Exception:
                raise BrowserFetchError('Browser unavailable after relaunch.') from None
        latest = [None]

        def track_response(response):
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                latest[0] = response

        page.on('response', track_response)
        try:
            latest[0] = page.goto(url, wait_until='domcontentloaded',
                                  timeout=settings.browser_navigation_timeout * 1000)
            deadline = time.monotonic() + settings.browser_challenge_wait
            while True:
                html = page.content()
                headers = latest[0].headers if latest[0] else {}
                reason = challenge_reason(html, headers)
                if not reason:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    diagnostic_id, ray_id = self._diagnostics(page, url, latest[0], reason)
                    raise BrowserChallengeError(
                        f'{reason} blocked the crawl. Open the URL in a normal browser to check access.',
                        diagnostic_id=diagnostic_id, ray_id=ray_id)
                page.wait_for_timeout(min(500, remaining * 1000))
            if latest[0] and latest[0].status >= 400:
                diagnostic_id, ray_id = self._diagnostics(page, url, latest[0], 'HTTP error')
                raise BrowserFetchError(f'Upstream returned HTTP {latest[0].status}.',
                                        diagnostic_id=diagnostic_id, ray_id=ray_id)
            return html
        except BrowserFetchError:
            raise
        except Exception:
            diagnostic_id, ray_id = self._diagnostics(page, url, latest[0], 'Navigation failed')
            # Playwright exception strings can contain URLs and proxy credentials.
            raise BrowserFetchError('Browser navigation failed or timed out.',
                                    diagnostic_id=diagnostic_id, ray_id=ray_id) from None
        finally:
            page.close()

    def _shutdown(self):
        for stop in (self._browser.close, self._pw.stop):
            try:
                stop()
            except Exception:
                logger.warning('Browser shutdown step failed.')

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._executor.submit(self._shutdown).result()
            finally:
                self._executor.shutdown()


class DriverFactory:
    @staticmethod
    def create_driver() -> BrowserDriver:
        # return UndetectedChromeBrowserDriver()
        return PlaywrightDriver()


if __name__ == "__main__":
    driver = DriverFactory().create_driver()
    url = "https://rargb.to/search/1/?search=2026&category[]=movies"
    result = driver.fetch(url)
    print(result)
