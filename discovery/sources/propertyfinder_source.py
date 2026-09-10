"""Property Finder agent source for Owner-CRM.
Drop-in API: scan(already_processed=None) -> list[dict].
"""
import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.propertyfinder.ae"
DIRECTORY_URL = os.getenv("PROPERTYFINDER_DIRECTORY_URL", BASE_URL + "/en/find-agent")
MAX_ADS = int(os.getenv("SCRAPER_MAX_ADS", "30"))
MAX_PAGES = int(os.getenv("PROPERTYFINDER_MAX_PAGES", "10"))
REQUEST_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "30"))
DELAY = float(os.getenv("SCRAPER_DELAY", "1.0"))
MIN_RENTALS = int(os.getenv("PROPERTYFINDER_MIN_RENTALS", "1"))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36"}


def normalize_phone(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("00971"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) in (9, 10):
        digits = "971" + digits[1:]
    if digits.startswith("971") and 11 <= len(digits) <= 12:
        return "+" + digits
    return None


def directory_url(page_number):
    if page_number <= 1:
        return DIRECTORY_URL
    separator = "&" if "?" in DIRECTORY_URL else "?"
    return f"{DIRECTORY_URL}{separator}page={page_number}"


def get_json_script(soup, *, script_id=None, script_type=None):
    attrs = {}
    if script_id:
        attrs["id"] = script_id
    if script_type:
        attrs["type"] = script_type
    tag = soup.find("script", attrs=attrs)
    if not tag:
        return None
    raw = tag.string or tag.get_text()
    return json.loads(raw) if raw else None


def collect_profile_urls(session, already_processed, target):
    processed = set() if callable(already_processed) else set(already_processed or ())
    is_processed = already_processed if callable(already_processed) else lambda url: url in processed
    urls, seen = [], set()

    for page_number in range(1, MAX_PAGES + 1):
        if len(urls) >= target:
            break
        url = directory_url(page_number)
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        page_urls = []
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if not re.fullmatch(r"/en/agent/[a-z0-9-]+-\d+/?", href, re.I):
                continue
            profile_url = urljoin(BASE_URL, href).rstrip("/")
            if profile_url in seen or is_processed(profile_url):
                continue
            seen.add(profile_url)
            page_urls.append(profile_url)
            urls.append(profile_url)
            if len(urls) >= target:
                break
        print(f"Property Finder directory page {page_number}: {len(page_urls)} new profiles")
        if not page_urls:
            break
        time.sleep(DELAY)
    return urls


def broker_name(broker):
    if isinstance(broker, dict):
        return broker.get("name") or broker.get("title") or ""
    return str(broker or "")


def list_text(value):
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("name") or item.get("title") or item.get("label")
            else:
                text = str(item)
            if text:
                parts.append(text)
        return ", ".join(parts)
    if isinstance(value, dict):
        return value.get("name") or value.get("title") or value.get("label") or ""
    return str(value or "")


def parse_profile(session, url):
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    next_data = get_json_script(soup, script_id="__NEXT_DATA__") or {}
    page_props = next_data.get("props", {}).get("pageProps", {})
    agent = page_props.get("agent") or {}
    if not isinstance(agent, dict) or not agent.get("name"):
        raise ValueError("Agent data not found in __NEXT_DATA__")

    rent_residential = int(agent.get("propertiesResidentialForRentCount") or 0)
    rent_commercial = int(agent.get("propertiesCommercialForRentCount") or 0)
    sale_residential = int(agent.get("propertiesResidentialForSaleCount") or 0)
    sale_commercial = int(agent.get("propertiesCommercialForSaleCount") or 0)
    rental_count = rent_residential + rent_commercial

    phone = normalize_phone(agent.get("phone")) or normalize_phone(agent.get("whatsappPhone"))
    agency = broker_name(agent.get("broker"))
    languages = list_text(agent.get("languages"))
    locations = list_text(agent.get("topLocations"))
    position = str(agent.get("position") or "")

    description_parts = [f"Agency: {agency}" if agency else None,
                         f"Position: {position}" if position else None,
                         f"Residential rentals: {rent_residential}",
                         f"Commercial rentals: {rent_commercial}",
                         f"Residential sales: {sale_residential}",
                         f"Commercial sales: {sale_commercial}",
                         f"Total properties: {int(agent.get('totalProperties') or 0)}",
                         f"Languages: {languages}" if languages else None,
                         f"Top locations: {locations}" if locations else None,
                         f"License: {agent.get('licenseNumber')}" if agent.get("licenseNumber") else None,
                         f"Verified: {'Yes' if agent.get('verified') else 'No'}",
                         f"SuperAgent: {'Yes' if agent.get('superagent') else 'No'}"]

    return {
        "title": str(agent.get("name")).strip(),
        "description": " | ".join(part for part in description_parts if part),
        "city": locations or "UAE",
        "source": "Property Finder",
        "url": url,
        "posted_at": None,
        "phone": phone,
        "contact_status": "phone_found" if phone else "no_contact_found",
        "rental_count": rental_count,
    }


def scan(already_processed=None):
    session = requests.Session()
    session.headers.update(HEADERS)
    results = []
    # Collect extra candidates because profiles with no rentals are skipped.
    target_candidates = min(MAX_ADS * 4, MAX_ADS + 100)
    try:
        urls = collect_profile_urls(session, already_processed, target_candidates)
        print(f"Collected {len(urls)} Property Finder profile URLs")
        for url in urls:
            if len(results) >= MAX_ADS:
                break
            try:
                agent = parse_profile(session, url)
                if agent.pop("rental_count", 0) < MIN_RENTALS:
                    print(f"Skip profile without rentals: {url}")
                    continue
                results.append(agent)
                print(f"Added {agent['title']}; phone={'yes' if agent['phone'] else 'no'}")
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                print(f"Skip {url}: {exc}")
            time.sleep(DELAY)
    finally:
        session.close()

    print(f"Property Finder scan completed: agents={len(results)}; phones={sum(bool(x.get('phone')) for x in results)}")
    return results


if __name__ == "__main__":
    for item in scan():
        print(item)
