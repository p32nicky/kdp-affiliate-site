import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import init_db, upsert_products, update_local_image
from app.scraper import scrape_kdp_pages
from app.imagegen import generate_for_product

logger = logging.getLogger(__name__)

_state = {
    "last_run_at": None,
    "last_inserted": 0,
    "last_error": None,
    "next_run_at": None,
    "running": False,
}
_lock = threading.Lock()


def run_ingest() -> None:
    settings = get_settings()
    with _lock:
        if _state["running"]:
            logger.info("Ingest already running, skipping")
            return
        _state["running"] = True

    try:
        logger.info("Starting KDP scrape (pages=%d)", settings.scrape_pages)
        products = scrape_kdp_pages(settings.affiliate_ref, settings.scrape_pages, settings.cf_session_cookie)
        inserted = upsert_products(settings.db_path, products)
        logger.info("Upserted %d products (%d new)", len(products), inserted)

        # Generate sales images for new products (cap at 30 per run)
        import os
        from app.db import list_products, update_local_image
        rows, _ = list_products(settings.db_path, per_page=50)
        generated = 0
        for row in rows:
            if not row["local_image_path"] and generated < 30:
                product = dict(row)
                path = generate_for_product(product, settings.images_dir)
                if path:
                    update_local_image(settings.db_path, row["url"], path)
                    generated += 1

        with _lock:
            _state["last_run_at"] = datetime.now(timezone.utc).isoformat()
            _state["last_inserted"] = inserted
            _state["last_error"] = None
    except Exception as e:
        logger.exception("Ingest failed: %s", e)
        with _lock:
            _state["last_error"] = str(e)
    finally:
        with _lock:
            _state["running"] = False


class SchedulerService:
    def __init__(self):
        self._scheduler = BackgroundScheduler()

    def start(self) -> None:
        settings = get_settings()
        trigger = CronTrigger(
            hour=settings.daily_hour,
            minute=settings.daily_minute,
            timezone=settings.timezone,
        )
        self._scheduler.add_job(run_ingest, trigger, id="daily_ingest", replace_existing=True)
        self._scheduler.start()
        job = self._scheduler.get_job("daily_ingest")
        if job:
            with _lock:
                _state["next_run_at"] = job.next_run_time.isoformat() if job.next_run_time else None
        logger.info("Scheduler started (daily %02d:%02d %s)", settings.daily_hour, settings.daily_minute, settings.timezone)

    def trigger_now(self) -> None:
        threading.Thread(target=run_ingest, daemon=True).start()

    def get_state(self) -> dict:
        with _lock:
            return dict(_state)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
