"""Fast paginated Property Finder company source for Owner-CRM.

Drop-in API:
    scan(already_processed=None, on_result=None) -> list[dict]

Reads the complete broker directory from /en/find-broker/search?page=N and
uses the structured __NEXT_DATA__ broker records. No browser is required.
"""
import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.propertyfinder.ae"
DIRECTORY_URL = os.getenv(
    "PROPERTYFINDER_DIRECTORY_URL",
    BASE_URL + "/en/find-broker/search",
)
MAX_ADS = max(1, int(os.getenv("SCRAPER_MAX_ADS", "30")))
MAX_PAGES = max(1, int(os.getenv("PROPERTYFINDER_MAX_PAGES", "198")))
REQUEST_TIMEOUT = max(5, int(os.getenv("SCRAPER_TIMEOUT", "30")))
DELAY = max(0.0, float(os.getenv("SCRAPER_DELAY", "0.2")))
MIN_RENTALS = max(1, int(os.getenv("PROPERTYFINDER_MIN_RENTALS", "1")))
RETRIES = max(0, int(os.getenv("PROPERTYFINDER_RETRIES", "2")))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AE,en;q=0.9",
}
BROKER_PATH_RE = re.compile(r"/en/broker/[a-z0-9-]+(?:-\d+){1,2}/?", re.I)


def build_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=RETRIES,
        connect=RETRIES,
        read=RETRIES,
        status=RETRIES,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET",)),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def normalize_phone(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("00971"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) in (9, 10):
        digits = "971" + digits[1:]
    if digits.startswith("971") and len(digits) in (11, 12):
        return "+" + digits
    return None


def parse_int(value):
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d[\d,]*", str(value))
    return int(match.group(0).replace(",", "")) if match else 0


def first(data, *keys, default=None):
    if not isinstance(data, dict):
        return default
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def page_url(number):
    if number <= 1:
        return DIRECTORY_URL
    separator = "&" if "?" in DIRECTORY_URL else "?"
    return f"{DIRECTORY_URL}{separator}page={number}"


def next_data(soup):
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag:
        return {}
    raw = tag.string or tag.get_text()
    if not raw:
        return {}
    return json.loads(raw)


def broker_container(page_data):
    props = page_data.get("props", {}).get("pageProps", {})
    brokers = props.get("brokers", {})
    if isinstance(brokers, dict) and isinstance(brokers.get("data"), list):
        return brokers
    return {}


def broker_url(broker):
    slug = str(first(broker, "urlSlug", "slug", default="") or "").strip("/")
    client_id = first(broker, "clientId", "id")
    if slug and client_id:
        return f"{BASE_URL}/en/broker/{slug}-{client_id}".rstrip("/")

    raw_url = first(broker, "url", "profileUrl", default="")
    if raw_url:
        url = urljoin(BASE_URL, str(raw_url)).split("?", 1)[0].rstrip("/")
        if BROKER_PATH_RE.search(url):
            return url
    return None


def extract_location(broker):
    value = first(broker, "location", "city", "emirate", default="")
    if isinstance(value, dict):
        value = first(value, "name", "title", "label", default="")
    return str(value or "UAE").strip() or "UAE"


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
    if company["email"]:
        parts.append(f"Email: {company['email']}")
    return " | ".join(parts)


def broker_to_result(broker):
    url = broker_url(broker)
    if not url:
        return None

    rentals = parse_int(first(
        broker,
        "propertiesResidentialForRentCount",
        "propertiesForRentCount",
        "rentCount",
    ))
    if rentals < MIN_RENTALS:
        return None

    phone = normalize_phone(first(broker, "phone", "telephone", "mobile"))
    company = {
        "title": str(first(broker, "name", "title", default="Unknown company")).strip(),
        "city": extract_location(broker),
        "url": url,
        "rentals": rentals,
        "sales": parse_int(first(
            broker,
            "propertiesResidentialForSaleCount",
            "propertiesForSaleCount",
            "saleCount",
        )),
        "active": parse_int(first(broker, "totalProperties", "activeListings")),
        "agents": parse_int(first(broker, "totalAgents", "agentsCount")),
        "superagents": parse_int(first(broker, "totalSuperAgents", "superAgentsCount")),
        "orn": str(first(broker, "orn", "licenseNumber", "registrationNumber", default="") or ""),
        "address": str(first(broker, "address", "officeAddress", default="") or ""),
        "email": str(first(broker, "email", default="") or ""),
    }
    return {
        "title": company["title"],
        "description": make_description(company),
        "city": company["city"],
        "source": "Property Finder",
        "url": company["url"],
        "posted_at": None,
        "phone": phone,
        "contact_status": "phone_found" if phone else "no_contact_found",
    }


def scan(already_processed=None, on_result=None):
    processed_set = set() if callable(already_processed) else set(already_processed or ())
    is_processed = (
        already_processed
        if callable(already_processed)
        else lambda url: url in processed_set
    )

    session = build_session()
    results = []
    seen = set()
    reported_total_pages = None

    try:
        page_number = 1
        while page_number <= MAX_PAGES and len(results) < MAX_ADS:
            response = session.get(page_url(page_number), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            container = broker_container(next_data(soup))
            brokers = container.get("data", [])
            meta = container.get("meta", {}) if isinstance(container.get("meta"), dict) else {}

            if reported_total_pages is None:
                reported_total_pages = parse_int(meta.get("totalPages")) or MAX_PAGES
            effective_last_page = min(MAX_PAGES, reported_total_pages)

            if not brokers:
                print(
                    f"Property Finder page {page_number}: no broker records; stopping",
                    flush=True,
                )
                break

            page_new = 0
            page_rental = 0
            page_duplicate = 0

            for broker in brokers:
                url = broker_url(broker)
                if not url:
                    continue
                if url in seen or is_processed(url):
                    page_duplicate += 1
                    continue
                seen.add(url)

                result = broker_to_result(broker)
                if result is None:
                    continue
                page_rental += 1

                if on_result is not None:
                    on_result(result)
                results.append(result)
                page_new += 1

                print(
                    f"Completed company: {result['title']}; "
                    f"phone={'yes' if result['phone'] else 'no'}; "
                    f"page={page_number}; total={len(results)}",
                    flush=True,
                )
                if len(results) >= MAX_ADS:
                    break

            print(
                f"Property Finder page {page_number}/{effective_last_page}: "
                f"profiles={len(brokers)}; rental={page_rental}; "
                f"duplicates={page_duplicate}; added={page_new}",
                flush=True,
            )

            if page_number >= effective_last_page:
                break
            page_number += 1
            if DELAY:
                time.sleep(DELAY)
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
