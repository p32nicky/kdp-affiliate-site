import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.db import upsert_products, update_local_image, list_products
from app.scraper import scrape_kdp_pages

logger = logging.getLogger(__name__)

_state: dict = {
    "last_run_at": None,
    "last_inserted": 0,
    "last_error": None,
}


def run_ingest() -> int:
    settings = get_settings()
    try:
        logger.info("Starting KDP scrape (pages=%d)", settings.scrape_pages)
        products = scrape_kdp_pages(
            settings.affiliate_ref,
            settings.scrape_pages,
            settings.cf_session_cookie,
        )
        inserted = upsert_products(settings.db_path, products)
        logger.info("Upserted %d products (%d new)", len(products), inserted)

        # Generate sales images only when filesystem is writable (local dev)
        try:
            from app.imagegen import generate_for_product
            rows, _ = list_products(settings.db_path, per_page=50)
            generated = 0
            for row in rows:
                lp = row["local_image_path"] if hasattr(row, "__getitem__") else getattr(row, "local_image_path", "")
                if not lp and generated < 30:
                    path = generate_for_product(dict(row), settings.images_dir)
                    if path:
                        update_local_image(settings.db_path, row["url"], path)
                        generated += 1
        except (OSError, PermissionError):
            pass  # read-only filesystem (Vercel) — skip image generation

        _state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        _state["last_inserted"] = inserted
        _state["last_error"] = None
        return inserted
    except Exception as e:
        logger.exception("Ingest failed: %s", e)
        _state["last_error"] = str(e)
        return 0


def get_state() -> dict:
    return dict(_state)
