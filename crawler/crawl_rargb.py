from bs4 import BeautifulSoup
from db.db import db
import time
import argparse
from browserdriver.driver import DriverFactory
import logging


logger = logging.getLogger(__name__)


def crawl_rargb(page, keyword, type="movies") -> bool:
    url = f"https://rargb.to/search/{page}/?search={keyword}&category[]={type}"

    driver = DriverFactory().create_driver()
    html = driver.fetch(url)
    logger.debug(f"✔️Fetched HTML content: {html[:500]}")

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"class": "lista2t"})

    if not table:
        logger.info("❌ Could not find result table. Cloudflare may need more delay.")
        logger.info(html[:500])
        return False

    items = []
    rows = table.find_all("tr")[1:]

    for r in rows:
        cols = r.find_all("td")
        if len(cols) < 2:
            logger.debug("❌ Not enough columns in row, skipping.")
            continue

        a = cols[1].find("a")
        if not a:
            logger.debug("❌ No link found in row, skipping.")
            continue

        items.append(
            {
                "filename": a.text.strip(),
                "url": "https://rargb.to" + a["href"],
                "size": cols[4].text.strip(),
                "type": "00" if type == "movies" else "01",
                "genre": cols[1].find("span").text.strip()
                if cols[1].find("span")
                else "",
            }
        )
        logger.debug(f"Found item: {items[-1]}")

    db.save_items(items)

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl RARBG for movies or TV shows.")
    parser.add_argument("--type", choices=["movies", "tvshows"], default="movies")
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
    crawl_rargb(keyword="2026", page=args.page, type=args.type)
