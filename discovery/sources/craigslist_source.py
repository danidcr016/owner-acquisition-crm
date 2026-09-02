"""Memory-bounded Craigslist source for Render 512 MB.
Drop-in API: scan(already_processed=None) -> list[dict].
"""
import gc
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://sandiego.craigslist.org/search/apa?query=furnished&s={offset}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
MAX_ADS = int(os.getenv("SCRAPER_MAX_ADS", "30"))
BATCH_SIZE = int(os.getenv("SCRAPER_BATCH_SIZE", "25"))
SEARCH_PAGE_SIZE = int(os.getenv("SCRAPER_SEARCH_PAGE_SIZE", "120"))
DELAY_BETWEEN_REQUESTS = float(os.getenv("SCRAPER_DELAY", "1.0"))
OFFSET_FILE = Path(os.getenv("SCRAPER_OFFSET_FILE", "craigslist_offset.txt"))
REQUEST_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "12"))
MAX_DESCRIPTION_CHARS = int(os.getenv("SCRAPER_MAX_DESCRIPTION_CHARS", "12000"))
MAX_EXTERNAL_PAGES_PER_AD = int(os.getenv("SCRAPER_EXTERNAL_PAGES", "1"))

PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.()\-]*)?(?:\(\s*)?([2-9]\d{2})(?:\s*\))?[\s.\-]*(\d{3})[\s.\-]*(\d{4})(?!\d)")
OBFUSCATED_RE = re.compile(r"\b([2-9]\d{2})\s*(?:-|\.|\s|\[at\]|at)\s*(\d{3})\s*(?:-|\.|\s)\s*(\d{4})\b", re.I)
CONTACT_WORDS = ("contact", "phone", "telephone", "call", "text", "leasing", "manager", "landlord", "apply", "rental")
PRIORITY_EXTERNAL = ("turbotenant", "rent", "lease", "property", "management", "apartment", "contact", "apply")


def memory_mb():
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        return proc.memory_info().rss / 1024 / 1024
    except Exception:
        return 0.0


def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01":
        return None
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def extract_phone_candidates(text):
    found = []
    for pattern in (PHONE_RE, OBFUSCATED_RE):
        for match in pattern.finditer(text or ""):
            phone = normalize_phone("".join(match.groups()))
            if phone and phone not in found:
                found.append(phone)
    return found


def extract_phone_from_text(text):
    phones = extract_phone_candidates(text)
    return phones[0] if phones else None


def extract_phone_from_html(html):
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select('a[href^="tel:"], a[data-phone], [itemprop="telephone"]'):
        phone = normalize_phone(tag.get("href", "").replace("tel:", "") or tag.get("data-phone", "") or tag.get_text(" ", strip=True))
        if phone:
            return phone
    for tag in soup.select('meta[itemprop="telephone"], meta[property*="phone" i]'):
        phone = normalize_phone(tag.get("content", ""))
        if phone:
            return phone
    return extract_phone_from_text(soup.get_text(" ", strip=True))


def relevant_external_links(html, base_url):
    soup = BeautifulSoup(html or "", "html.parser")
    links = []
    host = urlparse(base_url).netloc
    for tag in soup.select("a[href]"):
        href = urljoin(base_url, tag.get("href", "")).split("#", 1)[0]
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"} or parsed.netloc == host:
            continue
        haystack = (href + " " + tag.get_text(" ", strip=True)).lower()
        if any(k in haystack for k in PRIORITY_EXTERNAL) and href not in links:
            links.append(href)
    return links[:MAX_EXTERNAL_PAGES_PER_AD]


def phone_from_external(session, url):
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            return None
        # Hard cap prevents unexpectedly huge external pages staying in memory.
        parts, size = [], 0
        for chunk in response.iter_content(32768, decode_unicode=True):
            if not chunk:
                continue
            parts.append(chunk)
            size += len(chunk)
            if size >= 1_000_000:
                break
        html = "".join(parts)
        return extract_phone_from_html(html)
    except requests.RequestException:
        return None
    finally:
        try:
            response.close()
        except Exception:
            pass


def get_offset():
    try:
        return max(0, int(OFFSET_FILE.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return 0


def save_offset(value):
    OFFSET_FILE.write_text(str(max(0, value)), encoding="utf-8")


def collect_listings(session, already_processed, target):
    listings, seen = [], set()
    is_processed = already_processed if callable(already_processed) else lambda url: url in set(already_processed or ())
    offset = get_offset()
    pages_checked = 0
    while len(listings) < target and pages_checked < 20:
        url = BASE_URL.format(offset=offset)
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        response.close()
        rows = soup.select("li.cl-static-search-result, li.cl-search-result, .result-row")
        if not rows:
            rows = [a.parent for a in soup.select('a[href*="/apa/d/"]') if a.parent]
        added = 0
        for row in rows:
            link = row.select_one('a[href]')
            if not link:
                continue
            ad_url = urljoin(url, link.get("href", "")).split("?", 1)[0]
            if not ad_url or ad_url in seen or is_processed(ad_url):
                continue
            seen.add(ad_url)
            title_node = row.select_one(".title, .posting-title, .result-title") or link
            location = row.select_one(".location, .result-hood")
            listings.append({"url": ad_url, "title": title_node.get_text(" ", strip=True), "city": location.get_text(" ", strip=True).strip(" ()") if location else "San Diego"})
            added += 1
            if len(listings) >= target:
                break
        pages_checked += 1
        offset += SEARCH_PAGE_SIZE
        if added == 0:
            break
    return listings, offset


def request_detail(session, listing):
    response = session.get(listing["url"], timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    html = response.text
    response.close()
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("#postingbody")
    description = body.get_text(" ", strip=True) if body else ""
    description = description.replace("QR Code Link to This Post", "", 1).strip()[:MAX_DESCRIPTION_CHARS]
    time_tag = soup.select_one("time[datetime]")
    phone = extract_phone_from_html(str(body) if body else html)
    return {"description": description, "posted_at": time_tag.get("datetime") if time_tag else None, "phone": phone, "phone_source": "html" if phone else None, "html": html if not phone else None}


def reveal_phone(page, url):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=18000)
        # Check DOM before clicking.
        for selector in ('a[href^="tel:"]', '[itemprop="telephone"]', '.reply-tel-number', '.contact-phone'):
            loc = page.locator(selector).first
            if loc.count():
                phone = normalize_phone(loc.get_attribute("href") or loc.inner_text())
                if phone:
                    return phone, "dom"
        candidates = page.get_by_text(re.compile(r"(?:show|more|view).*contact|contact.*info|\+\s*info", re.I)).first
        if candidates.count():
            candidates.click(timeout=4000)
            page.wait_for_timeout(800)
            phone = extract_phone_from_text(page.locator("body").inner_text(timeout=4000))
            if phone:
                return phone, "contact_button"
        return None, None
    except (PlaywrightTimeoutError, Exception):
        return None, None


def launch_browser(playwright):
    return playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--disable-sync", "--no-first-run", "--mute-audio"])


def scan(already_processed=None):
    session = requests.Session()
    session.headers.update(HEADERS)
    listings, next_offset = collect_listings(session, already_processed, MAX_ADS)
    ads = []
    print(f"Collected {len(listings)} new URLs; RSS={memory_mb():.1f} MB")

    with sync_playwright() as playwright:
        for batch_start in range(0, len(listings), BATCH_SIZE):
            batch = listings[batch_start:batch_start + BATCH_SIZE]
            browser = context = page = None
            try:
                browser = launch_browser(playwright)
                context = browser.new_context(user_agent=HEADERS["User-Agent"], service_workers="block", java_script_enabled=True)
                page = context.new_page()
                # CDP blocking avoids a Python route callback per network request.
                cdp = context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.send("Network.setBlockedURLs", {"urls": ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.woff", "*.woff2", "*.ttf", "*.mp4", "*.webm"]})
                for listing in batch:
                    detail = {"description": "", "posted_at": None, "phone": None, "phone_source": None, "html": None}
                    try:
                        detail.update(request_detail(session, listing))
                        if not detail["phone"]:
                            phone, source = reveal_phone(page, listing["url"])
                            detail["phone"], detail["phone_source"] = phone, source
                        if not detail["phone"] and detail.get("html"):
                            for external in relevant_external_links(detail["html"], listing["url"]):
                                phone = phone_from_external(session, external)
                                if phone:
                                    detail["phone"], detail["phone_source"] = phone, "external"
                                    break
                        ads.append({"title": listing["title"], "description": detail["description"] or listing["title"], "city": listing["city"], "source": "Craigslist", "url": listing["url"], "posted_at": detail["posted_at"], "phone": detail["phone"]})
                    except requests.RequestException as exc:
                        print(f"Skip {listing['url']}: {exc}")
                    finally:
                        detail.clear()
                        try:
                            page.goto("about:blank", wait_until="commit", timeout=3000)
                        except Exception:
                            pass
                    time.sleep(DELAY_BETWEEN_REQUESTS)
            finally:
                for obj in (page, context, browser):
                    if obj is not None:
                        try: obj.close()
                        except Exception: pass
                del page, context, browser
                gc.collect()
                print(f"Batch {batch_start // BATCH_SIZE + 1} complete; ads={len(ads)}; RSS={memory_mb():.1f} MB")
    session.close()
    save_offset(next_offset)
    return ads


if __name__ == "__main__":
    result = scan()
    print(f"Processed {len(result)} ads; phones={sum(bool(x.get('phone')) for x in result)}; RSS={memory_mb():.1f} MB")
