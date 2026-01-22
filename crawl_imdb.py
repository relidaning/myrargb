from bs4 import BeautifulSoup
from db import db
import time
import argparse
from workflow import Workflow
from selenium_conf import MySeleniumConfig
import logging


logger = logging.getLogger(__name__)


def crawl_imdb(keyword):
    items = db.get_items(workflow=Workflow.SCORING)
    logger.info(f"Found {len(items)} items to update from IMDb.")

    selenium = MySeleniumConfig()
    driver = selenium.driver
    for item in items:
        url = f"https://m.imdb.com/find/?q={item['title']}&ref_=chttvtp_nv_srb_sm"
        driver.get(url)

        # Wait for Cloudflare to finish JS challenge
        time.sleep(8)

        html = driver.page_source

        soup = BeautifulSoup(html, "html.parser")
        ul = soup.find("ul", {"class": "ipc-metadata-list--base"})

        if not ul:
            logger.info(
                "❌ Could not find result table. Cloudflare may need more delay."
            )
            logger.info(html[:500])
            update_item = {
                "id": item["id"],
                "score": "unmatched",
            }
            db.update_item(update_item)
            continue

        try:
            rows = ul.find_all("li")

            if rows is None or len(rows) == 0:
                logger.debug(
                    "❌ Could not find result table. Cloudflare may need more delay."
                )
                logger.debug(html[:500])
                update_item = {
                    "id": item["id"],
                    "score": "unmatched",
                }
                db.update_item(update_item)
                continue

            for r in rows:
                poster = r.find("img", {"class": "ipc-image"})["src"]
                title = r.find("h3", {"class": "ipc-title__text"}).text
                score = r.find("span", {"class": "ipc-rating-star--rating"}).text
                year = r.find("span", {"class": "cli-title-metadata-item"}).text
                if year != keyword:
                    continue

                update_item = {
                    "id": item["id"],
                    "poster": poster,
                    "score": score,
                    "title": title,
                }
                db.update_item(update_item)
                break

        except Exception as e:
            logger.error(f"❌ Error processing item {item['title']}: {e}")
            update_item = {
                "id": item["id"],
                "score": "unmatched",
            }
            db.update_item(update_item)
            continue

    return True


if __name__ == "__main__":
    results = crawl_imdb("2026")
