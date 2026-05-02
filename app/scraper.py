import json
import re
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from curl_cffi import requests as cf_requests
from bs4 import BeautifulSoup

from app.config import CF_KDP_BASE_URL, CF_KDP_SUBS_URL, CF_PRODUCT_BASE

logger = logging.getLogger(__name__)

TIMEOUT = 20


def _slug_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "product" in parts:
        idx = parts.index("product")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-1] if parts else ""


def _affiliate_url(slug: str, ref: str) -> str:
    return f"{CF_PRODUCT_BASE}{slug}/ref/{ref}/"


def _extract_products_from_next_data(html: str) -> list[dict]:
    """Extract product data from Next.js __NEXT_DATA__ JSON blob."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    products = []
    # Walk the JSON tree looking for product arrays
    def _find_products(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict) and "slug" in item and "name" in item:
                    products.append(item)
                else:
                    _find_products(item, depth + 1)
        elif isinstance(obj, dict):
            for v in obj.values():
                _find_products(v, depth + 1)

    _find_products(data)
    return products


def _parse_next_product(raw: dict, ref: str) -> dict | None:
    slug = raw.get("slug", "")
    title = raw.get("name", "") or raw.get("title", "")
    if not slug or not title:
        return None

    url = f"{CF_PRODUCT_BASE}{slug}/"

    # Image: check various possible keys
    image_url = (
        raw.get("thumbnail")
        or raw.get("image")
        or raw.get("images", [{}])[0].get("src", "") if raw.get("images") else ""
        or ""
    )
    if isinstance(image_url, dict):
        image_url = image_url.get("src", "") or image_url.get("url", "")

    description = raw.get("short_description", "") or raw.get("description", "") or ""
    # Strip HTML from description
    if description:
        description = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)

    now = datetime.now(timezone.utc).isoformat()
    return {
        "title": title,
        "slug": slug,
        "url": url,
        "affiliate_url": _affiliate_url(slug, ref),
        "description": description[:500],
        "image_url": image_url,
        "local_image_path": "",
        "first_seen_at": now,
        "last_seen_at": now,
    }


def _parse_html_fallback(html: str, ref: str) -> list[dict]:
    """BS4 fallback: parse product cards from listing HTML.

    CF renders two <a> tags per product with the same href:
      1. Image link  (contains <img>, no text)
      2. Title link  (contains text, no img)
    We merge them by URL.
    """
    soup = BeautifulSoup(html, "html.parser")
    now = datetime.now(timezone.utc).isoformat()

    # slug → {title, image_url}
    seen: dict[str, dict] = {}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/product/" not in href:
            continue
        slug = _slug_from_url(href)
        if not slug:
            continue

        if slug not in seen:
            seen[slug] = {"title": "", "image_url": ""}

        # Image link
        img = a.find("img")
        if img and not seen[slug]["image_url"]:
            seen[slug]["image_url"] = (
                img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
            )
            if not seen[slug]["title"]:
                seen[slug]["title"] = img.get("alt", "")

        # Title link
        text = a.get_text(strip=True)
        if text and len(text) > 3 and not seen[slug]["title"]:
            seen[slug]["title"] = text

    products = []
    for slug, data in seen.items():
        if not data["title"]:
            continue
        url = f"{CF_PRODUCT_BASE}{slug}/"
        products.append({
            "title": data["title"],
            "slug": slug,
            "url": url,
            "affiliate_url": _affiliate_url(slug, ref),
            "description": "",
            "image_url": data["image_url"],
            "local_image_path": "",
            "first_seen_at": now,
            "last_seen_at": now,
        })

    return products


def scrape_product_description(url: str, client) -> str:
    """Fetch individual product page and extract description."""
    try:
        r = client.get(url, timeout=TIMEOUT, impersonate="chrome124")
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Try __NEXT_DATA__ first
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL
        )
        if match:
            try:
                data = json.loads(match.group(1))
                desc = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("product", {})
                    .get("short_description", "")
                )
                if desc:
                    return BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:500]
            except Exception:
                pass

        # Fallback: look for description div
        for sel in [
            "div.single-product-description",
            "div.product-description",
            'div[class*="description"]',
        ]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(" ", strip=True)[:500]
    except Exception as e:
        logger.debug("Description fetch failed for %s: %s", url, e)
    return ""


def scrape_kdp_pages(ref: str, pages: int = 3, session_cookie: str = "") -> list[dict]:
    """Scrape KDP interiors category pages and return product dicts."""
    all_products: list[dict] = []
    seen_urls: set[str] = set()

    # Use subscriptions page (more products) if session cookie provided, else public category
    if session_cookie:
        base_url = CF_KDP_SUBS_URL
        cookies = {"cookie": session_cookie}
        logger.info("Using subscriptions URL with session cookie")
    else:
        base_url = CF_KDP_BASE_URL
        cookies = {}
        logger.info("Using public category URL (set CF_SESSION_COOKIE env var for more results)")

    with cf_requests.Session() as client:
        if cookies:
            client.cookies.update(cookies)
        for page_num in range(1, pages + 1):
            if page_num == 1:
                url = base_url
            else:
                url = f"{base_url}page/{page_num}/"

            logger.info("Scraping page %d: %s", page_num, url)
            try:
                r = client.get(url, timeout=TIMEOUT, impersonate="chrome124")
                r.raise_for_status()
            except Exception as e:
                logger.warning("Failed to fetch page %d: %s", page_num, e)
                continue

            # Try Next.js data first
            products = _extract_products_from_next_data(r.text)
            if products:
                for raw in products:
                    parsed = _parse_next_product(raw, ref)
                    if parsed and parsed["url"] not in seen_urls:
                        seen_urls.add(parsed["url"])
                        all_products.append(parsed)
            else:
                # HTML fallback
                products = _parse_html_fallback(r.text, ref)
                for p in products:
                    if p["url"] not in seen_urls:
                        seen_urls.add(p["url"])
                        all_products.append(p)

            logger.info("Page %d: found %d products (total %d)", page_num, len(products), len(all_products))

        # Enrich descriptions for products that are missing them (up to 20)
        needs_desc = [p for p in all_products if not p["description"]][:20]
        for p in needs_desc:
            desc = scrape_product_description(p["url"], client)
            if desc:
                p["description"] = desc

    return all_products
