"""Fast Property Finder company source for Owner-CRM.

Drop-in API:
    scan(already_processed=None, on_result=None) -> list[dict]

Uses requests + BeautifulSoup only. Company phone numbers are read directly
from the public company profile link (data-testid="phone-btn" / tel: href).
"""
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.propertyfinder.ae"
DIRECTORY_URL = os.getenv(
    "PROPERTYFINDER_DIRECTORY_URL",
    BASE_URL + "/en/find-broker",
)
MAX_ADS = int(os.getenv("SCRAPER_MAX_ADS", "30"))
MAX_PAGES = int(os.getenv("PROPERTYFINDER_MAX_PAGES", "10"))
REQUEST_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "30"))
DELAY = float(os.getenv("SCRAPER_DELAY", "0.2"))
MIN_RENTALS = int(os.getenv("PROPERTYFINDER_MIN_RENTALS", "1"))
WORKERS = max(1, min(int(os.getenv("PROPERTYFINDER_WORKERS", "5")), 10))
RETRIES = max(0, int(os.getenv("PROPERTYFINDER_RETRIES", "2")))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AE,en;q=0.9",
}
BROKER_PATH_RE = re.compile(r"/en/broker/[a-z0-9-]+(?:-\d+){1,2}/?", re.I)
_thread_local = threading.local()


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
    adapter = HTTPAdapter(max_retries=retry, pool_connections=WORKERS, pool_maxsize=WORKERS)
    session.mount("https://", adapter)
    return session


def worker_session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = build_session()
        _thread_local.session = session
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
    if not isinstance(data, dict):
        return default
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def collect_companies(session, already_processed, target):
    processed = set() if callable(already_processed) else set(already_processed or ())
    is_processed = already_processed if callable(already_processed) else lambda url: url in processed
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
                    candidate = " ".join(ancestor.get_text(" ", strip=True).split())
                    if "for rent" in candidate.lower() and "agents" in candidate.lower():
                        card_text = candidate
                        break

            match = re.search(r"for\s+rent\s*:?\s*([\d,]+)", card_text, re.I)
            rentals = parse_int(match.group(1)) if match else 0
            if rentals < MIN_RENTALS:
                continue

            seen.add(url)
            companies.append({"url": url, "directory_text": card_text, "rentals": rentals})
            page_added += 1
            if len(companies) >= target:
                break

        print(
            f"Property Finder companies page {number}: {page_added} new rental companies",
            flush=True,
        )
        if page_added == 0:
            break
        if DELAY:
            time.sleep(DELAY)

    return companies


def extract_company_phone(soup):
    phone_link = (
        soup.find("a", attrs={"data-testid": "phone-btn"})
        or soup.find("a", attrs={"data-qa": "phone-button"})
    )
    if phone_link:
        phone = normalize_phone(phone_link.get("href"))
        if phone:
            return phone

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if href.lower().startswith("tel:"):
            phone = normalize_phone(href)
            if phone:
                return phone
    return None


def parse_company_profile(company):
    session = worker_session()
    response = session.get(company["url"], timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    phone = extract_company_phone(soup)
    page_props = json_script(soup, "__NEXT_DATA__").get("props", {}).get("pageProps", {})

    required = {"orn", "licenseNumber", "address", "activeListings", "agentsCount", "superAgentsCount"}
    candidates = list(deep_find_dict(page_props, required))
    data = max(candidates, key=lambda item: len(required.intersection(item.keys())), default={})

    heading = soup.find("h1")
    title = first(data, "name", "title", "brokerName", "companyName")
    if not title and heading:
        title = heading.get_text(" ", strip=True)
    if not title:
        title = company["directory_text"].split("Head office", 1)[0].strip()

    text = " ".join(soup.get_text(" ", strip=True).split())
    location_match = re.search(
        r"Location\s*:\s*([A-Za-z ]+?)(?:\s+Agents\s*:|\s+SuperAgents\s*:)",
        company["directory_text"], re.I,
    )
    raw_location = first(data, "city", "location", "emirate", default="")
    if isinstance(raw_location, dict):
        location = str(first(raw_location, "name", "title", default=""))
    else:
        location = str(raw_location or "")
    if not location and location_match:
        location = location_match.group(1).strip()

    def directory_count(label):
        match = re.search(rf"{label}\s*:?\s*([\d,]+)", company["directory_text"], re.I)
        return parse_int(match.group(1)) if match else 0

    orn = first(data, "orn", "licenseNumber", "registrationNumber", default="")
    if not orn:
        match = re.search(r"\bORN\s*(\d+)", text, re.I)
        orn = match.group(1) if match else ""

    address = first(data, "address", "officeAddress", default="")
    if isinstance(address, dict):
        address = first(address, "streetAddress", "name", "address", default="")
    if not address:
        match = re.search(
            r"Address\s*:\s*(.+?)(?:\s+Call Company|\s+Email Company|\s+About\s+)",
            text, re.I,
        )
        address = match.group(1).strip() if match else ""

    active = parse_int(first(data, "activeListings", "activeListingsCount", "totalProperties"))
    if not active:
        match = re.search(r"([\d,]+)\s+Active Listings", text, re.I)
        active = parse_int(match.group(1)) if match else 0

    description = first(data, "description", "about", "bio", default="")
    return {
        "title": str(title).strip(),
        "city": location or "UAE",
        "url": company["url"],
        "rentals": directory_count(r"for\s+rent") or company["rentals"],
        "sales": directory_count(r"for\s+sale"),
        "agents": directory_count("Agents") or parse_int(first(data, "agentsCount", "agentCount")),
        "superagents": directory_count("SuperAgents") or parse_int(first(data, "superAgentsCount", "superAgentCount")),
        "active": active,
        "orn": str(orn or ""),
        "address": str(address or ""),
        "about": " ".join(str(description or "").split())[:1500],
        "phone": phone,
    }


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


def to_result(company):
    phone = company.get("phone")
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
    directory_session = build_session()
    results = []
    try:
        candidates = collect_companies(directory_session, already_processed, MAX_ADS * 3)
        print(
            f"Collected {len(candidates)} Property Finder company profiles; workers={WORKERS}",
            flush=True,
        )

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(parse_company_profile, candidate): candidate for candidate in candidates}
            for future in as_completed(futures):
                candidate = futures[future]
                if len(results) >= MAX_ADS:
                    break
                try:
                    company = future.result()
                    result = to_result(company)
                    if on_result is not None:
                        on_result(result)
                    results.append(result)
                    print(
                        f"Completed company: {result['title']}; "
                        f"phone={'yes' if result['phone'] else 'no'}; total={len(results)}",
                        flush=True,
                    )
                except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                    print(f"Skip {candidate['url']}: {exc}", flush=True)

            for future in futures:
                if not future.done():
                    future.cancel()
    finally:
        directory_session.close()

    print(
        f"Property Finder scan completed: companies={len(results)}; "
        f"phones={sum(bool(item.get('phone')) for item in results)}",
        flush=True,
    )
    return results


if __name__ == "__main__":
    for item in scan():
        print(item)
