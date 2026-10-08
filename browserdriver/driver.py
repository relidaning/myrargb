import os
import time
import asyncio
import logging
from selenium import webdriver
from dotenv import load_dotenv
from abc import ABC, abstractmethod
import undetected_chromedriver as uc
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

load_dotenv()


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
            command_executor=os.environ.get("CHROME_URL", ""), options=options
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


_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-accelerated-2d-canvas",
    "--no-first-run",
    "--no-zygote",
    "--disable-gpu",
    "--window-size=1920,1080",
]

_CONTEXT_OPTIONS = dict(
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
    locale="en-US",
    timezone_id="America/New_York",
    permissions=["geolocation"],
    extra_http_headers={
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
)

_STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ],
    });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = {
        runtime: {
            PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
            PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
            RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
            OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
            OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
        },
    };
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
"""


class PlaywrightDriver(BrowserDriver):
    def __init__(self):
        # Playwright's sync API only works on the thread that started it, so
        # every call runs on this one worker thread. Callers on any thread
        # (Kafka consumer, Flask request) queue here and are served in turn.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        self._executor.submit(self._launch).result()

    def _launch(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True, args=_BROWSER_ARGS)
        self._context = self._browser.new_context(**_CONTEXT_OPTIONS)
        self._context.add_init_script(_STEALTH_SCRIPT)

    def _shutdown(self):
        for stop in (self._browser.close, self._pw.stop):
            try:
                stop()
            except Exception as e:
                logger.warning(f"[!] Playwright shutdown step failed: {e}")

    def fetch(self, url: str) -> str:
        return self._executor.submit(self._fetch, url).result()

    def _fetch(self, url: str) -> str:
        try:
            page = self._context.new_page()
        except Exception as e:
            # The browser is gone (crashed or killed). Relaunch once instead of
            # failing every later fetch until the app is restarted.
            logger.warning(f"[!] Browser unusable ({e}), relaunching.")
            self._shutdown()
            self._launch()
            page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for WAF challenge to resolve — this is the key fix
            # AWS WAF challenge runs JS and reloads; wait for the real content
            for _ in range(5):
                content = page.content()
                # Match the challenge page only: every real IMDb page also
                # mentions "awsWaf" (token cookie script), which used to keep
                # this loop waiting all 5 rounds (~20s) on already-loaded pages.
                if "challenge-container" in content or "AwsWafIntegration" in content:
                    page.wait_for_timeout(3000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                else:
                    break

            html = page.content()
        finally:
            page.close()
        return html

    def close(self):
        self._executor.submit(self._shutdown).result()
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
