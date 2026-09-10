"""Memory-bounded Dubizzle agency source for Render 512 MB.

Drop-in API:
    scan(already_processed=None) -> list[dict]

The source reads public agency cards from Dubizzle's UAE agency directory,
collects up to MAX_ADS agencies across result pages, opens each public Call
control, and returns Owner-CRM-compatible dictionaries.
"""

import gc
import os
import re
import time
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = os.getenv(
    "DUBIZZLE_AGENCIES_URL",
    "https://uae.dubizzle.com/property-agencies/",
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}
MAX_ADS = int(os.getenv("SCRAPER_MAX_ADS", "30"))
MAX_PAGES = int(os.getenv("DUBIZZLE_MAX_PAGES", "10"))
DELAY_BETWEEN_AGENCIES = float(os.getenv("SCRAPER_DELAY", "1.0"))
PAGE_TIMEOUT = int(os.getenv("DUBIZZLE_PAGE_TIMEOUT", "30000"))
PHONE_TIMEOUT = int(os.getenv("DUBIZZLE_PHONE_TIMEOUT", "6000"))

AGENTS_RE = re.compile(r"\b(\d[\d,]*)\s+Agents?\b", re.I)
PROPERTIES_RE = re.compile(r"\b(\d[\d,]*)\s+Properties\b", re.I)
VERIFIED_RE = re.compile(r"\b(\d[\d,]*)\s+Verified\b", re.I)

# UAE geographic and non-geographic service numbers commonly exposed by
# business contact dialogs. Boundary checks reduce false positives.
UAE_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?971[\s-]?(?:[2-9][\s-]?\d{7,8})|"
    r"(?:600|800)[\s-]?\d{5,7})(?!\d)",
    re.I,
)


class DubizzlePageError(RuntimeError):
    pass


def memory_mb():
    try:
        import psutil

        process = psutil.Process(os.getpid())
        return process.memory_info().rss / 1024 / 1024
    except Exception:
        return 0.0


def parse_number(pattern, text):
    match = pattern.search(text or "")
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def normalize_uae_phone(raw):
    if not raw:
        return None

    compact = re.sub(r"[^\d+]", "", raw)
    digits = re.sub(r"\D", "", compact)

    if digits.startswith("00971"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) in (9, 10):
        digits = "971" + digits[1:]

    if digits.startswith("971") and 11 <= len(digits) <= 12:
        return "+" + digits

    if digits.startswith(("600", "800")) and 8 <= len(digits) <= 10:
        return "+971" + digits

    return None


def extract_uae_phone(text):
    for match in UAE_PHONE_RE.finditer(text or ""):
        phone = normalize_uae_phone(match.group(0))
        if phone:
            return phone
    return None


def page_url(page_number):
    if page_number <= 1:
        return BASE_URL
    separator = "&" if "?" in BASE_URL else "?"
    return f"{BASE_URL}{separator}page={page_number}"


def launch_browser(playwright):
    return playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-sync",
            "--no-first-run",
            "--mute-audio",
        ],
    )


def block_heavy_assets(context, page):
    """Block non-essential assets without using a Python route callback."""
    cdp = context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send(
        "Network.setBlockedURLs",
        {
            "urls": [
                "*.png",
                "*.jpg",
                "*.jpeg",
                "*.gif",
                "*.webp",
                "*.svg",
                "*.woff",
                "*.woff2",
                "*.ttf",
                "*.mp4",
                "*.webm",
            ]
        },
    )


def wait_for_agency_cards(page):
    try:
        page.locator('a[href*="/property-agencies/"] h2').first.wait_for(
            state="visible",
            timeout=PAGE_TIMEOUT,
        )
    except PlaywrightTimeoutError as exc:
        title = page.title()
        body = page.locator("body").inner_text(timeout=5000)[:1000]
        raise DubizzlePageError(
            f"Agency cards were not found. title={title!r}; body={body!r}"
        ) from exc


def agency_cards(page):
    """Return card locators without relying on unstable Material UI classes."""
    headings = page.locator('a[href*="/property-agencies/"] h2')
    cards = []
    used_urls = set()

    for index in range(headings.count()):
        heading = headings.nth(index)
        name = heading.inner_text(timeout=3000).strip()
        if not name:
            continue

        link = heading.locator("xpath=ancestor::a[1]")
        href = link.get_attribute("href") if link.count() else None
        agency_url = urljoin(BASE_URL, href or "").split("?", 1)[0]
        if not agency_url or agency_url in used_urls:
            continue

        # Find the smallest ancestor that contains this heading plus the
        # visible agency metadata and the Call action.
        card = heading.locator(
            "xpath=ancestor::*["
            ".//*[self::button or self::a]["
            "translate(normalize-space(.), 'CALL', 'call')='call'"
            "] and .//*[contains(normalize-space(.), 'Properties')]"
            "][1]"
        )

        if not card.count():
            # Fallback for layouts where Call is rendered with role=button.
            card = heading.locator(
                "xpath=ancestor::*["
                ".//*[@role='button' and "
                "translate(normalize-space(.), 'CALL', 'call')='call']"
                " and .//*[contains(normalize-space(.), 'Properties')]"
                "][1]"
            )

        if not card.count():
            print(f"Skip card without a usable container: {name}")
            continue

        used_urls.add(agency_url)
        cards.append((card, name, agency_url))

    return cards


def parse_card(card, name, agency_url):
    text = card.inner_text(timeout=5000)
    agents_count = parse_number(AGENTS_RE, text)
    properties_count = parse_number(PROPERTIES_RE, text)
    verified_count = parse_number(VERIFIED_RE, text)

    service_areas = ""
    match = re.search(r"Serves\s+in\s*(.+?)(?:\n\s*Call\b|\n\s*Email\b|$)", text, re.I | re.S)
    if match:
        service_areas = " ".join(match.group(1).split())

    return {
        "title": name,
        "url": agency_url,
        "agents_count": agents_count,
        "properties_count": properties_count,
        "verified_count": verified_count,
        "service_areas": service_areas,
    }


def visible_dialog_text(page):
    texts = []
    selectors = (
        '[role="dialog"]:visible',
        '.MuiDialog-root:visible',
        '.MuiModal-root:visible',
    )
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            try:
                texts.append(locator.nth(index).inner_text(timeout=1000))
            except Exception:
                pass
    return "\n".join(texts)


def close_contact_dialog(page):
    selectors = (
        '[role="dialog"] button[aria-label*="close" i]',
        '.MuiDialog-root button[aria-label*="close" i]',
        '.MuiModal-root button[aria-label*="close" i]',
        '[role="dialog"] button:has-text("Close")',
    )
    for selector in selectors:
        button = page.locator(selector).last
        try:
            if button.count() and button.is_visible():
                button.click(timeout=1500)
                return
        except Exception:
            pass

    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def reveal_agency_phone(page, card, agency_name):
    call_button = card.get_by_text(re.compile(r"^\s*Call\s*$", re.I)).last
    if not call_button.count():
        return None, "no_call_button"

    try:
        call_button.click(timeout=5000)
        deadline = time.monotonic() + PHONE_TIMEOUT / 1000

        while time.monotonic() < deadline:
            dialog_text = visible_dialog_text(page)
            phone = extract_uae_phone(dialog_text)
            if phone:
                print(f"Phone found for {agency_name}: {phone}")
                return phone, "phone_found"

            page.wait_for_timeout(250)

        print(f"No phone exposed by Call dialog for {agency_name}")
        return None, "no_contact_found"
    except PlaywrightTimeoutError:
        print(f"Call button timed out for {agency_name}")
        return None, "no_contact_found"
    except Exception as exc:
        print(f"Call action failed for {agency_name}: {exc}")
        return None, "no_contact_found"
    finally:
        close_contact_dialog(page)
        try:
            page.wait_for_timeout(150)
        except Exception:
            pass


def make_description(agency):
    parts = []
    if agency["agents_count"] is not None:
        parts.append(f"Agents: {agency['agents_count']}")
    if agency["properties_count"] is not None:
        parts.append(f"Properties: {agency['properties_count']}")
    if agency["verified_count"] is not None:
        parts.append(f"Verified: {agency['verified_count']}")
    if agency["service_areas"]:
        parts.append(f"Serves in: {agency['service_areas']}")
    return " | ".join(parts) or agency["title"]


def scan(already_processed=None):
    processed_set = set() if callable(already_processed) else set(already_processed or ())
    is_processed = (
        already_processed
        if callable(already_processed)
        else lambda url: url in processed_set
    )

    ads = []
    seen_urls = set()

    with sync_playwright() as playwright:
        browser = context = page = None
        try:
            browser = launch_browser(playwright)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                service_workers="block",
                java_script_enabled=True,
                locale="en-AE",
            )
            page = context.new_page()
            block_heavy_assets(context, page)

            for current_page in range(1, MAX_PAGES + 1):
                if len(ads) >= MAX_ADS:
                    break

                url = page_url(current_page)
                print(f"Reading Dubizzle agencies page {current_page}: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                wait_for_agency_cards(page)

                cards = agency_cards(page)
                print(f"Found {len(cards)} agency cards on page {current_page}")
                if not cards:
                    break

                page_added = 0
                for card, name, agency_url in cards:
                    if len(ads) >= MAX_ADS:
                        break
                    if agency_url in seen_urls or is_processed(agency_url):
                        continue

                    seen_urls.add(agency_url)
                    agency = parse_card(card, name, agency_url)
                    phone, contact_status = reveal_agency_phone(page, card, name)

                    ads.append(
                        {
                            "title": agency["title"],
                            "description": make_description(agency),
                            "city": "UAE",
                            "source": "Dubizzle",
                            "url": agency["url"],
                            "posted_at": None,
                            "phone": phone,
                            "contact_status": contact_status,
                        }
                    )
                    page_added += 1
                    time.sleep(DELAY_BETWEEN_AGENCIES)

                print(
                    f"Dubizzle page {current_page} complete; added={page_added}; "
                    f"total={len(ads)}; RSS={memory_mb():.1f} MB"
                )

                # If a full page consisted only of already processed records,
                # continue to the next page instead of stopping prematurely.
                gc.collect()

        finally:
            for obj in (page, context, browser):
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass
            gc.collect()

    print(
        f"Dubizzle scan completed: agencies={len(ads)}; "
        f"phones={sum(bool(item.get('phone')) for item in ads)}; "
        f"RSS={memory_mb():.1f} MB"
    )
    return ads


if __name__ == "__main__":
    result = scan()
    for item in result:
        print(item)
