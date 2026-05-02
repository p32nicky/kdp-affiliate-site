import os
from dataclasses import dataclass

AFFILIATE_REF = "425959"
CF_KDP_BASE_URL = "https://www.creativefabrica.com/subscriptions/graphics/kdp-interiors/"
CF_KDP_SUBS_URL = CF_KDP_BASE_URL  # same URL, kept for compat
CF_PRODUCT_BASE = "https://www.creativefabrica.com/product/"


@dataclass(frozen=True)
class Settings:
    db_path: str
    images_dir: str
    affiliate_ref: str
    scrape_pages: int
    daily_hour: int
    daily_minute: int
    timezone: str
    site_title: str
    site_url: str
    cf_session_cookie: str  # Optional: wordpress_logged_in_* cookie from browser


def get_settings() -> Settings:
    return Settings(
        db_path=os.environ.get("DB_PATH", "./data/products.sqlite3"),
        images_dir=os.environ.get("IMAGES_DIR", "./app/static/images"),
        affiliate_ref=os.environ.get("AFFILIATE_REF", AFFILIATE_REF),
        scrape_pages=int(os.environ.get("SCRAPE_PAGES", "3")),
        daily_hour=int(os.environ.get("DAILY_HOUR", "7")),
        daily_minute=int(os.environ.get("DAILY_MINUTE", "0")),
        timezone=os.environ.get("TIMEZONE", "America/New_York"),
        site_title=os.environ.get("SITE_TITLE", "KDP Manuscript Templates"),
        # On Vercel, VERCEL_URL is auto-set to e.g. "your-app.vercel.app"
        site_url=os.environ.get(
            "SITE_URL",
            "https://" + os.environ["VERCEL_URL"] if "VERCEL_URL" in os.environ else "http://localhost:8000"
        ),
        cf_session_cookie=os.environ.get("CF_SESSION_COOKIE", ""),
    )
