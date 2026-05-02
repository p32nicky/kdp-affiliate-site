"""
Run this on your local PC to scrape CF and push to Neon.
Usage:
  1. Copy .env.example to .env and fill in your DATABASE_URL
  2. Run: .venv\Scripts\python sync.py
"""
import os
import sys

# Load .env file if present
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from app.config import get_settings
from app.db import init_db, upsert_products, update_local_image, list_products
from app.scraper import scrape_kdp_pages
from app.imagegen import generate_for_product

def main():
    settings = get_settings()

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    print(f"Initialising DB...")
    init_db(settings.db_path)

    print(f"Scraping CF KDP ({settings.scrape_pages} pages)...")
    products = scrape_kdp_pages(
        settings.affiliate_ref,
        settings.scrape_pages,
        settings.cf_session_cookie,
    )
    print(f"Scraped {len(products)} products")

    inserted = upsert_products(settings.db_path, products)
    print(f"Inserted {inserted} new products")

    # Images: use CF CDN URLs directly (no local storage needed)
    print("Done. CF CDN images will be used on the live site.")

if __name__ == "__main__":
    main()
