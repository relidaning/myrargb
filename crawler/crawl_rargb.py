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

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"class": "lista2t"})

    if not table:
        logger.error(
            "❌ Could not find result table. Cloudflare may need more delay.\n {html}"
        )
        return False

    items = []
    rows = table.find_all("tr")[1:]

    for r in rows:
        cols = r.find_all("td")

        a = cols[1].find("a")
        items.append(
            {
                "filename": a.text.strip(),
                "url": f"https://rargb.to{a['href']}",
                "size": cols[4].text.strip(),
                "type": "00" if type == "movies" else "01",
                "added": cols[3].text.strip(),
            }
        )
        logger.info(f"Found item: {items[-1]}")

    db.save_items(items)

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl RARBG for movies or TV shows.")
    parser.add_argument("--type", choices=["movies", "tvshows"], default="movies")
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
    crawl_rargb(keyword="2026", page=args.page, type=args.type)
