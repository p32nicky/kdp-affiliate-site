import logging
import os
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import init_db, list_products, get_latest_products, get_product_by_slug
from app.ingest import run_ingest, get_state

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

settings = get_settings()

# Create images dir only when filesystem is writable (local dev)
try:
    os.makedirs(settings.images_dir, exist_ok=True)
except OSError:
    pass

init_db(settings.db_path)

app = FastAPI(title=settings.site_title)

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str = Query("", alias="q"),
    page: int = Query(1, ge=1),
):
    per_page = 24
    rows, total = list_products(settings.db_path, query=q, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "products": rows,
        "query": q,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "site_title": settings.site_title,
        "status": get_state(),
    })


@app.get("/product/{slug}", response_class=HTMLResponse)
async def product_detail(request: Request, slug: str):
    product = get_product_by_slug(settings.db_path, slug)
    if not product:
        return HTMLResponse("Product not found", status_code=404)
    return templates.TemplateResponse("product.html", {
        "request": request,
        "p": product,
        "site_title": settings.site_title,
    })


@app.get("/feed.xml")
async def rss_feed():
    """RSS 2.0 feed — IFTTT watches this to auto-post to Pinterest."""
    products = get_latest_products(settings.db_path, limit=10)

    rss = Element("rss", version="2.0")
    rss.set("xmlns:media", "http://search.yahoo.com/mrss/")
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = settings.site_title
    SubElement(channel, "link").text = settings.site_url
    SubElement(channel, "description").text = "KDP Manuscript Templates from Creative Fabrica"
    SubElement(channel, "language").text = "en-us"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    for idx, p in enumerate(products):
        item = SubElement(channel, "item")
        product_page_url = f"{settings.site_url}/product/{p['slug']}"
        SubElement(item, "title").text = p["title"]
        SubElement(item, "link").text = product_page_url
        SubElement(item, "guid", isPermaLink="true").text = product_page_url
        desc = p["description"] or f"KDP interior template: {p['title']}"
        SubElement(item, "description").text = (
            f'{desc}<br/><a href="{p["affiliate_url"]}">Get it on Creative Fabrica →</a>'
        )

        # Image for Pinterest pin
        image_url = ""
        if p["local_image_path"]:
            image_url = f"{settings.site_url}{p['local_image_path']}"
        elif p["image_url"]:
            image_url = p["image_url"]

        if image_url:
            enc = SubElement(item, "enclosure")
            enc.set("url", image_url)
            enc.set("type", "image/jpeg")
            enc.set("length", "0")
            media = SubElement(item, "media:content")
            media.set("url", image_url)
            media.set("medium", "image")

        # Space pins 2 hours apart to avoid spam signals
        try:
            dt = datetime.fromisoformat((p["first_seen_at"] or "").replace("Z", "+00:00"))
            from datetime import timedelta
            dt = dt + timedelta(hours=idx * 2)
            pub_date = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except Exception:
            pub_date = ""
        SubElement(item, "pubDate").text = pub_date

    xml_bytes = tostring(rss, encoding="unicode", xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes
    return Response(content=xml_str, media_type="application/rss+xml")


@app.get("/api/cron")
async def cron_ingest():
    """Called daily by Vercel Cron (also works as manual trigger)."""
    inserted = run_ingest()
    return JSONResponse({"status": "ok", "inserted": inserted})


@app.post("/api/ingest")
async def trigger_ingest():
    """Manual trigger from the UI button."""
    inserted = run_ingest()
    return JSONResponse({"status": "ok", "inserted": inserted})


@app.get("/api/status")
async def get_status():
    return JSONResponse(get_state())
