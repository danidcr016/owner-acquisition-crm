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
    """
    Finds and ranks external contact links.

    Public property and contact pages are prioritised.
    Application and prescreener forms are ignored.
    """

    soup = BeautifulSoup(
        html or "",
        "html.parser"
    )

    base_host = urlparse(
        base_url
    ).netloc.lower()

    candidates = {}

    ignored_hosts = {
        "images.craigslist.org",
        "google.com",
        "www.google.com",
        "maps.google.com",
        "facebook.com",
        "www.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "twitter.com",
        "www.twitter.com",
        "x.com",
        "youtube.com",
        "www.youtube.com",
    }

    ignored_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".pdf",
        ".mp4",
        ".webm",
    )

    for position, tag in enumerate(
        soup.select("a[href]")
    ):

        href = urljoin(
            base_url,
            tag.get("href", "")
        ).split("#", 1)[0]

        parsed = urlparse(href)

        host = parsed.netloc.lower()
        path = parsed.path.lower()

        link_text = tag.get_text(
            " ",
            strip=True
        ).lower()

        combined_text = (
            f"{href} {link_text}"
        ).lower()

        # Ignore invalid or internal links
        if parsed.scheme not in {
            "http",
            "https"
        }:
            continue

        if not host:
            continue

        if host == base_host:
            continue

        # Ignore images, maps and social networks
        if host in ignored_hosts:
            continue

        if path.endswith(
            ignored_extensions
        ):
            continue

        # Ignore application and prescreener forms
        if any(
            term in combined_text
            for term in (
                "general-prescreener",
                "prescreener",
                "pre-screen",
                "application",
                "apply-now",
                "/apply",
            )
        ):
            continue

        score = 0

        # Highest priority:
        # TurboTenant public property page
        if (
            host == "rental.turbotenant.com"
            and path.startswith("/p/")
        ):
            score += 1000

        # Strong contact indicators in visible text
        if "contact the landlord" in link_text:
            score += 500

        if "contact the owner" in link_text:
            score += 500

        if "have questions" in link_text:
            score += 250

        if any(
            term in link_text
            for term in (
                "contact",
                "landlord",
                "owner",
                "phone",
                "call",
                "questions",
            )
        ):
            score += 180

        # Generic public contact or property pages
        if any(
            term in path
            for term in (
                "/contact",
                "/property",
                "/properties",
                "/listing",
                "/rental",
            )
        ):
            score += 120

        if any(
            term in host
            for term in (
                "turbotenant",
                "property",
                "rental",
                "rent",
                "leasing",
            )
        ):
            score += 80

        if any(
            term in combined_text
            for term in PRIORITY_EXTERNAL
        ):
            score += 30

        # Links inside the Craigslist description
        # are more relevant than global page links
        if tag.find_parent(
            id="postingbody"
        ) is not None:
            score += 100

        if score <= 0:
            continue

        candidate = (
            score,
            -position,
            href
        )

        previous_candidate = (
            candidates.get(href)
        )

        if (
            previous_candidate is None
            or candidate > previous_candidate
        ):
            candidates[href] = candidate

    ranked_candidates = sorted(
        candidates.values(),
        reverse=True
    )

    selected_links = [
        href
        for _, _, href in ranked_candidates[
            :MAX_EXTERNAL_PAGES_PER_AD
        ]
    ]

    return selected_links


def extract_phone_from_external_html(html, url):
    """Read public contact phones from external rental listing HTML."""
    phone = extract_phone_from_html(html)
    if phone:
        return phone, "external_html"

    host = urlparse(url).netloc.lower()
    trusted_script_hosts = {
        "rental.turbotenant.com",
        "www.turbotenant.com",
        "turbotenant.com",
    }
    if host not in trusted_script_hosts:
        return None, None

    soup = BeautifulSoup(html or "", "html.parser")
    contact_terms = (
        "phone", "telephone", "contactinformation",
        "contactphone", "listingspecificcontact"
    )
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text(" ", strip=True)
        if not script_text:
            continue
        lowered = script_text.lower()
        if not any(term in lowered for term in contact_terms):
            continue
        phone = extract_phone_from_text(script_text)
        if phone:
            return phone, "external_embedded_data"

    return None, None


def phone_from_external(session, url):
    response = None
    try:
        response = session.get(
            url,
            timeout=(5, REQUEST_TIMEOUT),
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type:
            return None, None

        parts, size = [], 0
        for chunk in response.iter_content(32768, decode_unicode=True):
            if not chunk:
                continue
            parts.append(chunk)
            size += len(chunk)
            if size >= 1_000_000:
                break

        html = "".join(parts)
        return extract_phone_from_external_html(html, response.url)
    except requests.RequestException as exc:
        print(f"External HTTP contact failed: {url}: {exc}")
        return None, None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def phone_from_external_rendered(page, url):
    """Use the existing Playwright page only if static external HTML fails."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=18000)
        selectors = (
            'a[href^="tel:"]', '[itemprop="telephone"]',
            '[data-phone]', '[data-telephone]',
            '.phone-number', '.contact-phone',
            '[aria-label*="phone" i]',
        )
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count():
                raw = (
                    locator.get_attribute("href")
                    or locator.get_attribute("data-phone")
                    or locator.get_attribute("data-telephone")
                    or locator.get_attribute("aria-label")
                    or locator.inner_text(timeout=2500)
                )
                phone = normalize_phone(raw)
                if phone:
                    return phone, "external_rendered_dom"

        phone = extract_phone_from_text(page.locator("body").inner_text(timeout=5000))
        if phone:
            return phone, "external_rendered_text"
        return None, None
    except Exception as exc:
        print(f"External rendered contact failed: {url}: {exc}")
        return None, None


def get_offset():
    try:
        return max(0, int(OFFSET_FILE.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return 0


def save_offset(value):
    OFFSET_FILE.write_text(str(max(0, value)), encoding="utf-8")


def collect_listings(session, already_processed, target):
    listings, seen = [], set()
    processed_set = set() if callable(already_processed) else set(already_processed or ())
    is_processed = already_processed if callable(already_processed) else lambda url: url in processed_set
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

    contact_marker_source = str(body) if body else html
    has_contact_control = bool(
        re.search(
            r"show\s+(?:contact\s+info|phone(?:\s+number)?)|"
            r"contact\s+(?:info|information)|reply-button|"
            r"show-contact|contactinfo|replylink",
            contact_marker_source,
            re.I,
        )
    )

    return {
        "description": description,
        "posted_at": time_tag.get("datetime") if time_tag else None,
        "phone": phone,
        "phone_source": "html" if phone else None,
        "has_contact_control": has_contact_control,
        "html": html if not phone else None,
    }


def reveal_phone(page, url, contact_expected=False):
    """Try the public contact button once and classify protected contacts.

    This detects anti-bot or human-verification pages but does not attempt to
    solve or bypass them.
    """
    dialog_messages = []

    def handle_dialog(dialog):
        dialog_messages.append(dialog.message or "")
        try:
            dialog.accept()
        except Exception:
            pass

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=18000)

        for selector in (
            'a[href^="tel:"]',
            '[itemprop="telephone"]',
            '.reply-tel-number',
            '.contact-phone',
        ):
            loc = page.locator(selector).first
            if loc.count():
                phone = normalize_phone(
                    loc.get_attribute("href") or loc.inner_text()
                )
                if phone:
                    return phone, "dom", "phone_found"

        candidate_selectors = (
            'button:has-text("show contact info")',
            'a:has-text("show contact info")',
            '[role="button"]:has-text("show contact info")',
            'button:has-text("show phone")',
            'a:has-text("show phone")',
            '.show-contact',
            '.show-contact-info',
            '.reply-button',
            '[data-action*="contact" i]',
            '[aria-label*="contact" i]',
        )

        candidates = None
        for selector in candidate_selectors:
            locator = page.locator(selector).first
            if locator.count():
                candidates = locator
                break

        if candidates is None:
            text_candidate = page.get_by_text(
                re.compile(
                    r"show\s+(?:contact\s+info|phone(?:\s+number)?)|"
                    r"contact\s+(?:info|information)|\+\s*info",
                    re.I,
                )
            ).first
            if text_candidate.count():
                candidates = text_candidate

        if candidates is None:
            if contact_expected:
                print("Craigslist contact control detected but requires human verification.")
                return None, "contact_button", "human_verification_required"
            return None, None, "no_contact_found"

        page.once("dialog", handle_dialog)
        candidates.click(timeout=4000)

        deadline = time.monotonic() + 5.0
        challenge_pattern = re.compile(
            r"captcha|recaptcha|verify (?:you are|that you are|human)|"
            r"human verification|security check|unusual traffic|"
            r"an error has occurred|access denied|are you a robot",
            re.I,
        )

        while time.monotonic() < deadline:
            if dialog_messages:
                message = " ".join(dialog_messages)
                if challenge_pattern.search(message) or message.strip():
                    print(f"Craigslist contact requires human verification: {message}")
                    return None, "contact_button", "human_verification_required"

            for selector in (
                'a[href^="tel:"]',
                '.reply-tel-number',
                '.contact-phone',
                '[itemprop="telephone"]',
            ):
                loc = page.locator(selector).first
                if loc.count():
                    phone = normalize_phone(
                        loc.get_attribute("href") or loc.inner_text()
                    )
                    if phone:
                        return phone, "contact_button", "phone_found"

            body_text = page.locator("body").inner_text(timeout=2500)
            phone = extract_phone_from_text(body_text)
            if phone:
                return phone, "contact_button", "phone_found"

            if challenge_pattern.search(body_text):
                print("Craigslist contact requires human verification.")
                return None, "contact_button", "human_verification_required"

            page.wait_for_timeout(250)

        return None, "contact_button", "human_verification_required"

    except PlaywrightTimeoutError:
        return None, "contact_button", "human_verification_required"
    except Exception as exc:
        print(f"Craigslist contact button failed: {exc}")
        return None, "contact_button", "human_verification_required"


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
                    detail = {"description": "", "posted_at": None, "phone": None, "phone_source": None, "contact_status": "no_contact_found", "has_contact_control": False, "html": None}
                    try:
                        detail.update(request_detail(session, listing))
                        if detail["phone"]:
                            detail["contact_status"] = "phone_found"
                        else:
                            phone, source, contact_status = reveal_phone(
                                page,
                                listing["url"],
                                contact_expected=detail.get("has_contact_control", False),
                            )
                            detail["phone"] = phone
                            detail["phone_source"] = source
                            detail["contact_status"] = contact_status
                        if not detail["phone"] and detail.get("html"):
                            external_urls = relevant_external_links(
                                detail["html"], listing["url"]
                            )
                            if external_urls:
                                print(f"Relevant external contact URL selected: {external_urls[0]}")

                            for external in external_urls:
                                phone, source = phone_from_external(session, external)
                                if not phone:
                                    phone, source = phone_from_external_rendered(page, external)
                                if phone:
                                    detail["phone"] = phone
                                    detail["phone_source"] = source
                                    detail["contact_status"] = "phone_found"
                                    print(f"Phone found via {source}: {phone}")
                                    break

                            if (
                                not detail["phone"]
                                and external_urls
                                and detail["contact_status"] != "human_verification_required"
                            ):
                                detail["contact_status"] = "external_contact_found"
                        ads.append({"title": listing["title"], "description": detail["description"] or listing["title"], "city": listing["city"], "source": "Craigslist", "url": listing["url"], "posted_at": detail["posted_at"], "phone": detail["phone"], "contact_status": detail["contact_status"]})
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
