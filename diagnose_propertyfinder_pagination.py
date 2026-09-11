"""Diagnose Property Finder broker-directory pagination.

Run in PowerShell from C:\Projects\Owner-CRM:
    python .\diagnose_propertyfinder_pagination.py

This diagnostic does not modify Owner-CRM or its database.
"""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.propertyfinder.ae"
DIRECTORY_URL = BASE_URL + "/en/find-broker"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AE,en;q=0.9",
}
BROKER_RE = re.compile(r"/en/broker/[a-z0-9-]+(?:-\d+){1,2}/?", re.I)
KEY_RE = re.compile(r"page|pagination|cursor|offset|limit|next|previous|total|broker", re.I)

TEST_URLS = [
    DIRECTORY_URL,
    DIRECTORY_URL + "?page=2",
    DIRECTORY_URL + "?page_number=2",
    DIRECTORY_URL + "?pageNumber=2",
    DIRECTORY_URL + "?offset=20",
    DIRECTORY_URL + "?start=20",
    DIRECTORY_URL + "?from=20",
]


def broker_urls(soup):
    found = []
    for link in soup.find_all("a", href=True):
        href = link.get("href", "").split("?", 1)[0]
        if BROKER_RE.fullmatch(href):
            url = urljoin(BASE_URL, href).rstrip("/")
            if url not in found:
                found.append(url)
    return found


def walk(value, path="root", output=None, depth=0):
    if output is None:
        output = []
    if depth > 20:
        return output
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if KEY_RE.search(str(key)):
                preview = repr(child)
                output.append({"path": child_path, "preview": preview[:1500]})
            walk(child, child_path, output, depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value[:100]):
            walk(child, f"{path}[{index}]", output, depth + 1)
    return output


def inspect_url(session, url):
    response = session.get(url, timeout=30, allow_redirects=True)
    soup = BeautifulSoup(response.text, "html.parser")
    urls = broker_urls(soup)
    pagination_links = []
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        text = " ".join(link.get_text(" ", strip=True).split())
        rel = " ".join(link.get("rel", []))
        if KEY_RE.search(href + " " + text + " " + rel):
            pagination_links.append({"text": text, "href": href, "rel": rel})

    next_data = {}
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag:
        raw = tag.string or tag.get_text()
        if raw:
            try:
                next_data = json.loads(raw)
            except json.JSONDecodeError:
                pass

    return {
        "requested_url": url,
        "final_url": response.url,
        "status": response.status_code,
        "bytes": len(response.content),
        "html_sha256": hashlib.sha256(response.content).hexdigest(),
        "broker_count": len(urls),
        "broker_urls": urls,
        "pagination_links": pagination_links[:200],
        "next_data_matches": walk(next_data)[:500],
    }


def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    results = []
    try:
        for url in TEST_URLS:
            print("Testing:", url, flush=True)
            item = inspect_url(session, url)
            results.append(item)
            print(
                " status=", item["status"],
                " final=", item["final_url"],
                " brokers=", item["broker_count"],
                " hash=", item["html_sha256"][:12],
                flush=True,
            )
    finally:
        session.close()

    baseline = set(results[0]["broker_urls"]) if results else set()
    for item in results:
        current = set(item["broker_urls"])
        item["new_vs_first"] = sorted(current - baseline)
        item["same_broker_set_as_first"] = current == baseline

    report = {
        "results": results,
        "summary": [
            {
                "requested_url": item["requested_url"],
                "final_url": item["final_url"],
                "status": item["status"],
                "broker_count": item["broker_count"],
                "same_broker_set_as_first": item["same_broker_set_as_first"],
                "new_vs_first_count": len(item["new_vs_first"]),
            }
            for item in results
        ],
    }

    output = Path("propertyfinder_pagination_report.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== PAGINATION SUMMARY ===")
    for item in report["summary"]:
        print(
            f"{item['requested_url']} | status={item['status']} | "
            f"brokers={item['broker_count']} | "
            f"same_as_first={item['same_broker_set_as_first']} | "
            f"new={item['new_vs_first_count']}"
        )

    first = results[0] if results else {}
    print("\nPagination-looking links on first response:")
    links = first.get("pagination_links", [])
    if not links:
        print("NONE")
    for link in links[:40]:
        print(" -", repr(link["text"]), link["href"], "rel=", link["rel"])

    print("\nInteresting __NEXT_DATA__ paths:")
    matches = first.get("next_data_matches", [])
    if not matches:
        print("NONE")
    for match in matches[:80]:
        print(" -", match["path"], "=>", match["preview"][:400])

    print("\nFull report:", output.resolve())


if __name__ == "__main__":
    main()
