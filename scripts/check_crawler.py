#!/usr/bin/env python3
"""Fetch one catalog page without Kafka, model loading, or database writes."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from bs4 import BeautifulSoup
from browserdriver.driver import DriverFactory
from browserdriver.errors import BrowserFetchError, UnexpectedPageError
from crawler import RargbCrawler
from utils.settings import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=RargbCrawler.page_url(1),
                        help='Page to inspect (defaults to the current catalog URL)')
    args = parser.parse_args()
    driver = None
    try:
        driver = DriverFactory.create_driver()
        html = driver.fetch(args.url)
        soup = BeautifulSoup(html, 'html.parser')
        try:
            table = soup.find('table', class_='lista2t')
            if table is None:
                raise UnexpectedPageError('Page loaded, but the expected catalog table is missing.')
            print(f'Catalog accessible: {max(0, len(table.find_all("tr")) - 1)} rows.')
            return 0
        finally:
            soup.decompose()
    except BrowserFetchError as error:
        print(str(error), file=sys.stderr)
        if error.diagnostic_id:
            print(f'Diagnostics: {settings.browser_diagnostics_path / error.diagnostic_id} (.json / .png)',
                  file=sys.stderr)
        if error.ray_id:
            print(f'Cloudflare Ray ID: {error.ray_id}', file=sys.stderr)
        return 1
    finally:
        if driver:
            driver.close()


if __name__ == '__main__':
    raise SystemExit(main())
