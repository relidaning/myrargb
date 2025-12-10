from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By


class SeleniumDriver:
    
    
    def __init__(self):
        options = webdriver.ChromeOptions()
        # MUST run with real UI, Cloudflare blocks headless
        # comment the next line if you want visible browser
        # options.add_argument("--headless=new")  
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        # options.add_argument("--disable-dev-shm-usage")

        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )

    
    def __del__(self):
        self.driver.quit()
        
        
driver = SeleniumDriver().driver