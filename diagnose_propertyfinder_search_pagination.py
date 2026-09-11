r"""Diagnose pagination on Property Finder's full broker search.
Run in PowerShell from C:\Projects\Owner-CRM:
    python .\diagnose_propertyfinder_search_pagination.py
"""
import json
import re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE = "https://www.propertyfinder.ae"
SEARCH = BASE + "/en/find-broker/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36",
    "Accept-Language": "en-AE,en;q=0.9",
}
BROKER_RE = re.compile(r"/en/broker/[a-z0-9-]+(?:-\d+){1,2}/?", re.I)
TEST_URLS = [
    SEARCH,
    SEARCH + "?page=2",
    SEARCH + "?page=3",
    SEARCH + "?page_number=2",
    SEARCH + "?pageNumber=2",
    SEARCH + "?offset=20",
    SEARCH + "?start=20",
    SEARCH + "?from=20",
]

def urls_from_html(soup):
    values=[]
    for a in soup.find_all("a", href=True):
        href=a["href"].split("?",1)[0]
        if BROKER_RE.fullmatch(href):
            url=urljoin(BASE,href).rstrip("/")
            if url not in values:
                values.append(url)
    return values

def next_data(soup):
    tag=soup.find("script",id="__NEXT_DATA__")
    if not tag:
        return {}
    raw=tag.string or tag.get_text()
    try:
        return json.loads(raw)
    except Exception:
        return {}

def find_broker_container(value,path="root"):
    found=[]
    if isinstance(value,dict):
        if isinstance(value.get("data"),list) and value["data"] and isinstance(value["data"][0],dict):
            first=value["data"][0]
            if any(k in first for k in ("clientId","urlSlug","propertiesResidentialForRentCount")):
                found.append((path,value))
        for k,v in value.items():
            found.extend(find_broker_container(v,f"{path}.{k}"))
    elif isinstance(value,list):
        for i,v in enumerate(value[:100]):
            found.extend(find_broker_container(v,f"{path}[{i}]"))
    return found

def main():
    session=requests.Session(); session.headers.update(HEADERS)
    results=[]
    for url in TEST_URLS:
        r=session.get(url,timeout=30)
        soup=BeautifulSoup(r.text,"html.parser")
        html_urls=urls_from_html(soup)
        nd=next_data(soup)
        containers=find_broker_container(nd)
        container_path=""
        broker_data=[]
        metadata={}
        if containers:
            container_path,container=containers[0]
            broker_data=container.get("data",[])
            metadata={k:v for k,v in container.items() if k!="data"}
        json_urls=[]
        for b in broker_data:
            slug=b.get("urlSlug")
            cid=b.get("clientId") or b.get("id")
            if slug and cid:
                json_urls.append(f"{BASE}/en/broker/{slug}-{cid}")
        all_urls=list(dict.fromkeys(html_urls+json_urls))
        item={
            "requested":url,"final":r.url,"status":r.status_code,
            "html_brokers":len(html_urls),"json_brokers":len(broker_data),
            "broker_urls":all_urls,"container_path":container_path,
            "metadata":metadata,
        }
        results.append(item)
        print(f"{url} | status={r.status_code} | html={len(html_urls)} | json={len(broker_data)}",flush=True)
    base=set(results[0]["broker_urls"])
    print("\n=== SEARCH PAGINATION SUMMARY ===")
    for item in results:
        current=set(item["broker_urls"])
        print(f"{item['requested']} | final={item['final']} | brokers={len(current)} | same_as_first={current==base} | new={len(current-base)}")
        if current-base:
            print(" first new:",sorted(current-base)[:3])
    print("\nBroker container path:",results[0]["container_path"] or "NONE")
    print("Broker metadata:",json.dumps(results[0]["metadata"],ensure_ascii=False)[:3000])
    out=Path("propertyfinder_search_pagination_report.json")
    out.write_text(json.dumps({"results":results},ensure_ascii=False,indent=2),encoding="utf-8")
    print("Full report:",out.resolve())

if __name__=="__main__":
    main()
