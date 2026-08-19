import time
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


BASE_URL = (
    "https://sandiego.craigslist.org/search/apa"
    "?query=furnished"
    "&s={offset}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


# =========================================================
# CONFIGURATION
# =========================================================

# Número de anuncios que procesamos en cada ejecución
MAX_ADS = 10

# Archivo donde guardamos la posición del último lote
OFFSET_FILE = "craigslist_offset.txt"

# Segundos de espera entre anuncios
DELAY_BETWEEN_REQUESTS = 1.5


# =========================================================
# GET AD DETAIL
# =========================================================

def get_ad_detail(page, url):
    """
    Abre un anuncio de Craigslist con Playwright.

    Extrae:
    - descripción
    - fecha de publicación
    - teléfono

    Si existe "show contact info", lo pulsa.
    Craigslist puede recargar la página y revelar
    el teléfono dentro de la descripción.
    """

    try:

        print(f"\nOpening: {url}")

        # =====================================================
        # OPEN AD
        # =====================================================

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=20000
        )

        page.wait_for_timeout(2000)

        # =====================================================
        # POSTED DATE
        # =====================================================

        posted_at = None

        time_tag = page.locator("time").first

        if time_tag.count() > 0:

            posted_at = time_tag.get_attribute(
                "datetime"
            )

        # =====================================================
        # INITIAL DESCRIPTION
        # =====================================================

        body = page.locator("#postingbody")

        description = ""

        if body.count() > 0:

            description = body.inner_text().strip()

            prefix = "QR Code Link to This Post"

            if description.startswith(prefix):

                description = (
                    description[len(prefix):]
                    .strip()
                )

        # =====================================================
        # FIND CONTACT INFO
        # =====================================================

        print(
            "Looking for contact info..."
        )

        contact_element = page.get_by_text(
            "show contact info",
            exact=True
        ).first

        contact_count = contact_element.count()

        print(
            f"Contact info elements found: {contact_count}"
        )

        # =====================================================
        # CLICK CONTACT INFO
        # =====================================================

        if contact_count > 0:

            print(
                "Contact info FOUND"
            )

            try:

                print(
                    "Clicking contact info..."
                )

                contact_element.click(
                    timeout=5000
                )

                print(
                    "Click successful"
                )

                # =================================================
                # WAIT FOR POSSIBLE RELOAD
                # =================================================

                try:

                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=10000
                    )

                except Exception:

                    pass

                # =================================================
                # WAIT FOR CONTACT TO APPEAR
                # =================================================

                page.wait_for_timeout(3000)

                print(
                    "Finished waiting after contact click"
                )

            except Exception as e:

                print(
                    "CONTACT CLICK ERROR:",
                    repr(e)
                )

        else:

            print(
                "Contact info NOT FOUND"
            )

        # =====================================================
        # READ DESCRIPTION AGAIN
        # =====================================================

        body = page.locator(
            "#postingbody"
        )

        updated_description = ""

        if body.count() > 0:

            updated_description = (
                body.inner_text().strip()
            )

        print(
            "Description after contact click:"
        )

        print(
            updated_description[:3000]
        )

        # =====================================================
        # FIND PHONE
        # =====================================================

        phone = None

        # =====================================================
        # FIRST: TEL LINK
        # =====================================================

        phone_link = page.locator(
            'a[href^="tel:"]'
        ).first

        if phone_link.count() > 0:

            href = phone_link.get_attribute(
                "href"
            )

            if href:

                phone = (
                    href
                    .replace(
                        "tel:",
                        ""
                    )
                    .strip()
                )

                print(
                    "PHONE FOUND FROM TEL LINK:",
                    phone
                )

        # =====================================================
        # SECOND: PHONE INSIDE DESCRIPTION
        # =====================================================

        if not phone:

            phone_matches = re.findall(
                r"""
                (?<!\d)
                (?:\+?1[\s.\-]?)?
                \(?\d{3}\)?[\s.\-]?
                \d{3}[\s.\-]?
                \d{4}
                (?!\d)
                """,
                updated_description,
                re.VERBOSE
            )

            if phone_matches:

                phone = (
                    phone_matches[0]
                    .strip()
                )

                print(
                    "PHONE FOUND IN DESCRIPTION:",
                    phone
                )

            else:

                print(
                    "No phone found in description."
                )

        # =====================================================
        # RESULT
        # =====================================================

        return {
            "description": (
                updated_description
                or description
            ),
            "posted_at": posted_at,
            "phone": phone
        }

    except Exception as e:

        print(
            "Error procesando anuncio:",
            url,
            repr(e)
        )

        return {
            "description": "",
            "posted_at": None,
            "phone": None
        }


# =========================================================
# OFFSET
# =========================================================

def get_offset():
    """
    Obtiene el offset actual.
    """

    try:

        with open(
            OFFSET_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return int(
                f.read().strip()
            )

    except (
        FileNotFoundError,
        ValueError
    ):

        return 0


def save_offset(offset):
    """
    Guarda el offset para el siguiente scan.
    """

    with open(
        OFFSET_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            str(offset)
        )


# =========================================================
# SCAN
# =========================================================

def scan():
    """
    Busca 10 anuncios empezando desde el offset actual.

    Ejecución 1:
        anuncios 0-9

    Ejecución 2:
        anuncios 10-19

    Ejecución 3:
        anuncios 20-29

    etc.
    """

    # =====================================================
    # GET CURRENT OFFSET
    # =====================================================

    offset = get_offset()

    print(
        f"Craigslist scan starting at offset: {offset}"
    )

    # =====================================================
    # SEARCH URL
    # =====================================================

    search_url = BASE_URL.format(
        offset=offset
    )

    print(
        f"Search URL: {search_url}"
    )

    response = requests.get(
        search_url,
        headers=HEADERS,
        timeout=10
    )

    if response.status_code != 200:

        print(
            "Craigslist devolvió status:",
            response.status_code
        )

        return []

    # =====================================================
    # PARSE SEARCH RESULTS
    # =====================================================

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    listings = soup.select(
        "li.cl-static-search-result"
    )

    print(
        f"Listings found on search page: {len(listings)}"
    )

    ads = []

    # =====================================================
    # PLAYWRIGHT
    # =====================================================

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            user_agent=HEADERS["User-Agent"]
        )

        try:

            # =================================================
            # PROCESS CURRENT BATCH
            # =================================================

            for listing in listings[:MAX_ADS]:

                # =============================================
                # REAL AD URL
                # =============================================

                link_tag = listing.find(
                    "a",
                    href=True
                )

                if link_tag is None:

                    continue

                ad_url = urljoin(
                    search_url,
                    link_tag["href"]
                )

                # =============================================
                # TITLE
                # =============================================

                title_div = listing.find(
                    "div",
                    class_="title"
                )

                title = (
                    title_div.get_text(
                        strip=True
                    )
                    if title_div
                    else ""
                )

                # =============================================
                # CITY / LOCATION
                # =============================================

                location_div = listing.find(
                    "div",
                    class_="location"
                )

                city = (
                    location_div.get_text(
                        strip=True
                    )
                    if location_div
                    else "San Diego"
                )

                # =============================================
                # GET DETAIL
                # =============================================

                detail = get_ad_detail(
                    page,
                    ad_url
                )

                # =============================================
                # FINAL AD
                # =============================================

                ad = {
                    "title": title,

                    "description": (
                        detail["description"]
                        or title
                    ),

                    "city": city,

                    "source": "Craigslist",

                    "url": ad_url,

                    "posted_at": (
                        detail["posted_at"]
                    ),

                    "phone": (
                        detail["phone"]
                    )
                }

                ads.append(
                    ad
                )

                # =============================================
                # LOG
                # =============================================

                print(
                    f"Processed: {title}"
                )

                print(
                    f"Phone: {detail['phone']}"
                )

                # =============================================
                # DELAY
                # =============================================

                time.sleep(
                    DELAY_BETWEEN_REQUESTS
                )

        finally:

            browser.close()

    # =====================================================
    # SAVE NEXT OFFSET
    # =====================================================

    next_offset = offset + MAX_ADS

    save_offset(
        next_offset
    )

    print(
        f"Craigslist offset updated: "
        f"{offset} -> {next_offset}"
    )

    print(
        f"Craigslist scan completed. "
        f"{len(ads)} ads processed."
    )

    return ads


# =========================================================
# DIRECT EXECUTION
# =========================================================

if __name__ == "__main__":

    ads = scan()

    print(
        "Anuncios extraídos:",
        len(ads)
    )

    for ad in ads:

        print(
            ad
        )