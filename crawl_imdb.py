from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from db import db
import time
import argparse
from workflow import Workflow
import logging as logger


logger.basicConfig(level=logger.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


def crawl_imdb(keyword):
    items = db.get_items(workflow=Workflow.SCORING)
    logger.debug(f"Found {len(items)} items to update from IMDb.")

    for item in items:
    
      url = f"https://m.imdb.com/find/?q={item['title']}&ref_=chttvtp_nv_srb_sm"
      options = webdriver.ChromeOptions()
      # MUST run with real UI, Cloudflare blocks headless
      # comment the next line if you want visible browser
      # options.add_argument("--headless=new")  
      options.add_argument("--disable-blink-features=AutomationControlled")
      options.add_argument("--disable-gpu")
      options.add_argument("--no-sandbox")

      driver = webdriver.Chrome(
          service=Service(ChromeDriverManager().install()),
          options=options
      )

      driver.get(url)

      # Wait for Cloudflare to finish JS challenge
      time.sleep(1)

      html = driver.page_source
      driver.quit()

      soup = BeautifulSoup(html, "html.parser")
      ul = soup.find("ul", {"class": "ipc-metadata-list--base"})

      if not ul:
        logger.debug("❌ Could not find result table. Cloudflare may need more delay.")
        logger.debug(html[:500])
        update_item = {
          "id": item["id"],
          "score": 'unmatched',
        }
        db.update_item(update_item)
        continue   
      
      try:
        rows = ul.find_all("li")

        if rows is None or len(rows) == 0:
          logger.debug("❌ Could not find result table. Cloudflare may need more delay.")
          logger.debug(html[:500])
          update_item = {
            "id": item["id"],
            "score": 'unmatched',
          }
          db.update_item(update_item)
          continue

        for r in rows:
          poster = r.find("img", {'class': 'ipc-image'})['src']
          title = r.find('h3', {'class': 'ipc-title__text'}).text
          score = r.find('span', {'class': 'ipc-rating-star--rating'}).text 
          year = r.find('span', {'class': 'cli-title-metadata-item'}).text
          if year != keyword:
            continue
                
          update_item = {
            "id": item["id"],
            "poster": poster,
            "score": score,
            "title": title
          }
          db.update_item(update_item)
          break
                
      except Exception as e:
        logger.debug(f"❌ Error processing item {item['title']}: {e}")
        update_item = {
          "id": item["id"],
          "score": 'unmatched',
        }
        db.update_item(update_item)
        continue    
      
    return True


if __name__ == "__main__":
    results = crawl_imdb('2026')
