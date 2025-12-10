from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
from db import db
import time
import argparse


def crawl_rargb(page, keyword, type='movies'):
    
    url = f"https://rargb.to/search/{page}/?search={keyword}&category[]={type}"
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
    table = soup.find("table", {"class": "lista2t"})

    if not table:
        print("❌ Could not find result table. Cloudflare may need more delay.")
        print(html[:500])
        return []

    items = []
    rows = table.find_all("tr")[1:]

    for r in rows:
        cols = r.find_all("td")
        if len(cols) < 2:
            continue

        a = cols[1].find("a")
        if not a:
            continue

        items.append({
            "filename": a.text.strip(),
            "url": "https://rargb.to" + a["href"],
            "size": cols[4].text.strip(),
            "type": '00' if type == 'movies' else '01',
            "genre": cols[1].find('span').text.strip() if cols[1].find('span') else ''
        })

    db.save_items(items)
    
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl RARBG for movies or TV shows.")
    parser.add_argument('--type', choices=['movies', 'tvshows'], default='movies')
    parser.add_argument('--page', type=int, default=1)
    args = parser.parse_args()
    results = crawl_rargb(page=args.page, type=args.type)
    db.save_items(results)
