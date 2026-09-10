"""Property Finder company source for Owner-CRM.

Drop-in API:
    scan(already_processed=None) -> list[dict]

Discovers UAE real-estate companies from /en/find-broker, keeps companies
with rental inventory, opens each public company profile, reveals the public
"Call Company" number, and returns Owner-CRM-compatible dictionaries.
"""
import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.propertyfinder.ae"
DIRECTORY_URL = os.getenv(
    "PROPERTYFINDER_DIRECTORY_URL",
    BASE_URL + "/en/find-broker",
)
MAX_ADS = int(os.getenv("SCRAPER_MAX_ADS", "30"))
MAX_PAGES = int(os.getenv("PROPERTYFINDER_MAX_PAGES", "10"))
REQUEST_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "30"))
PAGE_TIMEOUT = int(os.getenv("PROPERTYFINDER_PAGE_TIMEOUT", "30000"))
PHONE_TIMEOUT = int(os.getenv("PROPERTYFINDER_PHONE_TIMEOUT", "7000"))
DELAY = float(os.getenv("SCRAPER_DELAY", "1.0"))
MIN_RENTALS = int(os.getenv("PROPERTYFINDER_MIN_RENTALS", "1"))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36"
    )
}
BROKER_PATH_RE = re.compile(r"/en/broker/[a-z0-9-]+(?:-\d+){1,2}/?", re.I)
UAE_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?971|00971|0)[\s()-]*\d(?:[\s()-]*\d){7,9}(?!\d)"
)


def normalize_phone(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("00971"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) in (9, 10):
        digits = "971" + digits[1:]
    if digits.startswith("971") and 11 <= len(digits) <= 12:
        return "+" + digits
    return None


def extract_phone(text):
    for match in UAE_PHONE_RE.finditer(text or ""):
        phone = normalize_phone(match.group(0))
        if phone:
            return phone
    return None


def page_url(number):
    if number <= 1:
        return DIRECTORY_URL
    separator = "&" if "?" in DIRECTORY_URL else "?"
    return f"{DIRECTORY_URL}{separator}page={number}"


def json_script(soup, script_id):
    tag = soup.find("script", id=script_id)
    if not tag:
        return {}
    raw = tag.string or tag.get_text()
    return json.loads(raw) if raw else {}


def deep_find_dict(value, required_keys):
    if isinstance(value, dict):
        if required_keys.intersection(value.keys()):
            yield value
        for child in value.values():
            yield from deep_find_dict(child, required_keys)
    elif isinstance(value, list):
        for child in value:
            yield from deep_find_dict(child, required_keys)


def parse_int(value):
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d[\d,]*", str(value))
    return int(match.group(0).replace(",", "")) if match else 0


def first(data, *keys, default=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def collect_companies(session, already_processed, target):
    processed = set() if callable(already_processed) else set(already_processed or ())
    is_processed = (
        already_processed
        if callable(already_processed)
        else lambda url: url in processed
    )
    companies, seen = [], set()

    for number in range(1, MAX_PAGES + 1):
        if len(companies) >= target:
            break

        response = session.get(page_url(number), timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        page_added = 0

        for link in soup.find_all("a", href=True):
            href = link.get("href", "").split("?", 1)[0]
            if not BROKER_PATH_RE.fullmatch(href):
                continue

            url = urljoin(BASE_URL, href).rstrip("/")
            if url in seen or is_processed(url):
                continue

            card_text = " ".join(link.parent.get_text(" ", strip=True).split())
            if "for rent" not in card_text.lower():
                ancestor = link
                for _ in range(5):
                    ancestor = ancestor.parent
                    if ancestor is None:
                        break
                    candidate = " ".join(
                        ancestor.get_text(" ", strip=True).split()
                    )
                    if (
                        "for rent" in candidate.lower()
                        and "agents" in candidate.lower()
                    ):
                        card_text = candidate
                        break

            rent_match = re.search(
                r"for\s+rent\s*:?\s*([\d,]+)",
                card_text,
                re.I,
            )
            rentals = parse_int(rent_match.group(1)) if rent_match else 0
            if rentals < MIN_RENTALS:
                continue

            seen.add(url)
            companies.append(
                {
                    "url": url,
                    "directory_text": card_text,
                    "rentals": rentals,
                }
            )
            page_added += 1

            if len(companies) >= target:
                break

        print(
            f"Property Finder companies page {number}: "
            f"{page_added} new rental companies",
            flush=True,
        )

        if page_added == 0:
            break

        time.sleep(DELAY)

    return companies


def parse_company_profile(session, company):
    response = session.get(company["url"], timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    next_data = json_script(soup, "__NEXT_DATA__")
    page_props = next_data.get("props", {}).get("pageProps", {})

    required = {
        "orn",
        "licenseNumber",
        "address",
        "activeListings",
        "agentsCount",
        "superAgentsCount",
    }
    candidates = list(deep_find_dict(page_props, required))
    data = max(
        candidates,
        key=lambda item: len(required.intersection(item.keys())),
        default={},
    )

    heading = soup.find("h1")
    title = first(data, "name", "title", "brokerName", "companyName")
    if not title and heading:
        title = heading.get_text(" ", strip=True)
    if not title:
        title = company["directory_text"].split("Head office", 1)[0].strip()

    text = " ".join(soup.get_text(" ", strip=True).split())
    location_match = re.search(
        r"Location\s*:\s*([A-Za-z ]+?)"
        r"(?:\s+Agents\s*:|\s+SuperAgents\s*:)",
        company["directory_text"],
        re.I,
    )

    raw_location = first(data, "city", "location", "emirate", default="")
    if isinstance(raw_location, dict):
        location = str(first(raw_location, "name", "title", default=""))
    else:
        location = str(raw_location or "")
    if not location and location_match:
        location = location_match.group(1).strip()

    rent_match = re.search(
        r"for\s+rent\s*:?\s*([\d,]+)",
        company["directory_text"],
        re.I,
    )
    sale_match = re.search(
        r"for\s+sale\s*:?\s*([\d,]+)",
        company["directory_text"],
        re.I,
    )
    agents_match = re.search(
        r"Agents\s*:\s*([\d,]+)",
        company["directory_text"],
        re.I,
    )
    super_match = re.search(
        r"SuperAgents\s*:\s*([\d,]+)",
        company["directory_text"],
        re.I,
    )

    orn = first(data, "orn", "licenseNumber", "registrationNumber", default="")
    if not orn:
        match = re.search(r"\bORN\s*(\d+)", text, re.I)
        orn = match.group(1) if match else ""

    address = first(data, "address", "officeAddress", default="")
    if isinstance(address, dict):
        address = first(
            address,
            "streetAddress",
            "name",
            "address",
            default="",
        )
    if not address:
        match = re.search(
            r"Address\s*:\s*(.+?)"
            r"(?:\s+Call Company|\s+Email Company|\s+About\s+)",
            text,
            re.I,
        )
        address = match.group(1).strip() if match else ""

    active = parse_int(
        first(data, "activeListings", "activeListingsCount", "totalProperties")
    )
    if not active:
        match = re.search(r"([\d,]+)\s+Active Listings", text, re.I)
        active = parse_int(match.group(1)) if match else 0

    description = first(data, "description", "about", "bio", default="")

    return {
        "title": str(title).strip(),
        "city": location or "UAE",
        "url": company["url"],
        "rentals": (
            parse_int(rent_match.group(1))
            if rent_match
            else company["rentals"]
        ),
        "sales": parse_int(sale_match.group(1)) if sale_match else 0,
        "agents": (
            parse_int(agents_match.group(1))
            if agents_match
            else parse_int(first(data, "agentsCount", "agentCount"))
        ),
        "superagents": (
            parse_int(super_match.group(1))
            if super_match
            else parse_int(first(data, "superAgentsCount", "superAgentCount"))
        ),
        "active": active,
        "orn": str(orn or ""),
        "address": str(address or ""),
        "about": " ".join(str(description or "").split())[:1500],
    }


def reveal_company_phone(page, url, company_name):
    try:
        print(f"Navigating Chromium to: {url}", flush=True)

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        print(
            f"Chromium loaded company page: {page.url}",
            flush=True,
        )

        button = page.get_by_text(
            re.compile(r"^\s*Call Company\s*$", re.I)
        ).first
        button.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        print(
            f"Call Company button visible for: {company_name}",
            flush=True,
        )

        try:
            button.click(timeout=5000, no_wait_after=True)
        except Exception:
            button.evaluate("element => element.click()")

        deadline = time.monotonic() + PHONE_TIMEOUT / 1000
        while time.monotonic() < deadline:
            for locator in (
                page.locator('a[href^="tel:"]').first,
                page.get_by_text(UAE_PHONE_RE).first,
            ):
                try:
                    if locator.count():
                        raw = (
                            locator.get_attribute("href")
                            or locator.inner_text(timeout=1000)
                        )
                        phone = normalize_phone(raw)
                        if phone:
                            print(
                                f"Company phone found for {company_name}: {phone}",
                                flush=True,
                            )
                            return phone
                except Exception:
                    pass

            phone = extract_phone(
                page.locator("body").inner_text(timeout=2000)
            )
            if phone:
                print(
                    f"Company phone found for {company_name}: {phone}",
                    flush=True,
                )
                return phone

            page.wait_for_timeout(250)

    except PlaywrightTimeoutError:
        print(
            f"Call Company timed out for {company_name}",
            flush=True,
        )
    except Exception as exc:
        print(
            f"Call Company failed for {company_name}: {exc}",
            flush=True,
        )

    return None


def make_description(company):
    parts = [
        f"For rent: {company['rentals']}",
        f"For sale: {company['sales']}",
        f"Active listings: {company['active']}",
        f"Agents: {company['agents']}",
        f"SuperAgents: {company['superagents']}",
    ]
    if company["orn"]:
        parts.append(f"ORN: {company['orn']}")
    if company["address"]:
        parts.append(f"Address: {company['address']}")
    if company["about"]:
        parts.append(f"About: {company['about']}")
    return " | ".join(parts)


def scan(already_processed=None):
    session = requests.Session()
    session.headers.update(HEADERS)
    results = []

    try:
        candidates = collect_companies(
            session,
            already_processed,
            MAX_ADS * 3,
        )
        print(
            f"Collected {len(candidates)} Property Finder company profiles",
            flush=True,
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-sync",
                    "--no-first-run",
                    "--mute-audio",
                ],
            )

            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="en-AE",
                service_workers="block",
                java_script_enabled=True,
                viewport={"width": 1280, "height": 720},
            )

            page = context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT)

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

            try:
                for candidate in candidates:
                    if len(results) >= MAX_ADS:
                        break

                    print(
                        f"Processing company candidate: {candidate['url']}",
                        flush=True,
                    )

                    try:
                        company = parse_company_profile(session, candidate)

                        print(
                            f"Company profile parsed: {company['title']}",
                            flush=True,
                        )
                        print(
                            f"Opening Call Company for: {company['title']}",
                            flush=True,
                        )

                        phone = reveal_company_phone(
                            page,
                            company["url"],
                            company["title"],
                        )

                        results.append(
                            {
                                "title": company["title"],
                                "description": make_description(company),
                                "city": company["city"],
                                "source": "Property Finder",
                                "url": company["url"],
                                "posted_at": None,
                                "phone": phone,
                                "contact_status": (
                                    "phone_found"
                                    if phone
                                    else "no_contact_found"
                                ),
                            }
                        )

                        print(
                            f"Added company: {company['title']}; "
                            f"phone={'yes' if phone else 'no'}",
                            flush=True,
                        )

                    except (
                        requests.RequestException,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        print(
                            f"Skip {candidate['url']}: {exc}",
                            flush=True,
                        )

                    time.sleep(DELAY)

            finally:
                context.close()
                browser.close()

    finally:
        session.close()

    print(
        f"Property Finder scan completed: companies={len(results)}; "
        f"phones={sum(bool(item.get('phone')) for item in results)}",
        flush=True,
    )
    return results


if __name__ == "__main__":
    for item in scan():
        print(item)
