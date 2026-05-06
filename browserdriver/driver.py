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


class DriverFactory:
    @staticmethod
    def create_driver() -> BrowserDriver:
        return UndetectedChromeBrowserDriver()


if __name__ == "__main__":
    driver = DriverFactory().create_driver()
    url = f"https://rargb.to/search/1/?search=2026&category[]=movies"
    result = driver.fetch(url)
    print(result)
