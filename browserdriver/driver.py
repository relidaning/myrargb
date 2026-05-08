import logging

logger = logging.getLogger(__name__)
from dotenv import load_dotenv

load_dotenv()

from abc import ABC, abstractmethod


class BrowserDriver(ABC):
    @abstractmethod
    def fetch(self, url: str) -> str: ...


from selenium import webdriver
import time
import os


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


import undetected_chromedriver as uc


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
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu",  # counterintuitively helps in containers
                "--window-size=1920,1080",
            ],
        )
        self.context = self.browser.new_context(
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
        self._inject_stealth_scripts()

    def _inject_stealth_scripts(self):
        self.context.add_init_script("""
            // Remove webdriver flag
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // Fake plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ],
            });

            // Fake languages
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

            // Add window.chrome
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

            // Permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters)
            );
        """)

    def fetch(self, url: str) -> str:
        page = self.context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for WAF challenge to resolve — this is the key fix
            # AWS WAF challenge runs JS and reloads; wait for the real content
            for _ in range(5):
                content = page.content()
                if "challenge-container" in content or "awswaf" in content.lower():
                    page.wait_for_timeout(3000)  # Let JS challenge execute
                    page.wait_for_load_state("networkidle", timeout=15000)
                else:
                    break

            html = page.content()
        finally:
            page.close()
        return html


class DriverFactory:
    @staticmethod
    def create_driver() -> BrowserDriver:
        # return UndetectedChromeBrowserDriver()
        return PlaywrightDriver()


if __name__ == "__main__":
    driver = DriverFactory().create_driver()
    url = f"https://rargb.to/search/1/?search=2026&category[]=movies"
    result = driver.fetch(url)
    print(result)
