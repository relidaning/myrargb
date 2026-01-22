from selenium import webdriver
import logging


logger = logging.getLogger(__name__)

    
class MySeleniumConfig:


    def __init__(self):
        options = webdriver.ChromeOptions()
        # MUST run with real UI, Cloudflare blocks headless
        # comment the next line if you want visible browser
        options.add_argument("--headless")  
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("--window-size=1920,1080")
        # options.add_argument("--disable-dev-shm-usage")

        
        logger.info(" [v] Selenium WebDriver initialized.")

        self.driver = webdriver.Remote(
            command_executor="http://localhost:4444",
            options=options
        )


    def __del__(self):
        self.driver.quit()
        logger.info(" [x] Selenium WebDriver closed.")  
