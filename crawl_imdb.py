from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
from db import db
import time
import argparse


def crawl_imdb():
    items = db.get_items(is_training=False, limit=10, sql='and score is null')
    print(f"Found {len(items)} items to update from IMDb.")
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
      time.sleep(2)

      html = driver.page_source
      driver.quit()

      soup = BeautifulSoup(html, "html.parser")
      ul = soup.find("ul", {"class": "ipc-metadata-list--base"})

      if not ul:
          print("❌ Could not find result table. Cloudflare may need more delay.")
          print(html[:500])
          return []    
      
      rows = ul.find_all("li")
      r= rows[0]
      poster = r.find("img", {'class': 'ipc-image'})['src']
      title = r.find('h3', {'class': 'ipc-title__text'}).text
      score = r.find('span', {'class': 'ipc-rating-star--rating'}).text 
      
      update_item = {
          "id": item["id"],
          "poster": poster,
          "score": score,
          "title": title
      }
      db.update_item(update_item)
    return items


if __name__ == "__main__":
    results = crawl_imdb()
    #db.save_items(results)
