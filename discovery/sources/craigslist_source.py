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

# Número máximo de anuncios NUEVOS que procesamos
# en cada ejecución.
MAX_ADS = 30

# Archivo donde guardamos la posición del último lote.
OFFSET_FILE = "craigslist_offset.txt"

# Segundos de espera entre anuncios.
DELAY_BETWEEN_REQUESTS = 1.5

# Máximo de páginas externas que consultamos por anuncio.
# Lo dejamos en 1 para proteger RAM/tiempo de Render.
MAX_EXTERNAL_PAGES_PER_AD = 1

# Timeout para páginas externas.
EXTERNAL_REQUEST_TIMEOUT = 8


# =========================================================
# PHONE EXTRACTION
# =========================================================

def extract_phone_from_text(text):
    """
    Busca teléfonos estadounidenses dentro de un texto.

    Ejemplos detectados:

    619-555-1234
    (619) 555-1234
    619.555.1234
    619 555 1234
    +1 619-555-1234
    1-619-555-1234
    """

    if not text:
        return None

    phone_pattern = re.compile(
        r"""
        (?<!\d)
        (?:\+?1[\s.\-]?)?
        \(?\d{3}\)?
        [\s.\-]?
        \d{3}
        [\s.\-]?
        \d{4}
        (?!\d)
        """,
        re.VERBOSE
    )

    matches = phone_pattern.findall(text)

    if matches:
        return matches[0].strip()

    return None


def extract_phone_from_html(html):
    """
    Busca teléfono primero en enlaces tel:
    y después dentro del texto HTML.
    """

    if not html:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # =====================================================
    # FIRST: TEL LINKS
    # =====================================================

    tel_link = soup.select_one(
        'a[href^="tel:"]'
    )

    if tel_link:

        href = tel_link.get(
            "href",
            ""
        )

        phone = (
            href
            .replace("tel:", "")
            .strip()
        )

        if phone:
            return phone

    # =====================================================
    # SECOND: PAGE TEXT
    # =====================================================

    text = soup.get_text(
        " ",
        strip=True
    )

    return extract_phone_from_text(
        text
    )


# =========================================================
# EXTERNAL URL DETECTION
# =========================================================

def is_relevant_external_url(url):
    """
    Decide si un enlace externo parece relevante para
    encontrar información de contacto de un propietario
    o property manager.

    NO abrimos enlaces externos indiscriminadamente.

    Priorizamos:
    - TurboTenant
    - páginas de alquiler
    - property management
    - landlord/contact/application pages
    """

    if not url:
        return False

    url_lower = url.lower()

    # =====================================================
    # IGNORE NON-HTTP LINKS
    # =====================================================

    if not (
        url_lower.startswith("http://")
        or url_lower.startswith("https://")
    ):
        return False

    # =====================================================
    # IGNORE SOCIAL / GENERIC LINKS
    # =====================================================

    ignored_domains = {
        "facebook.com",
        "www.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "twitter.com",
        "www.twitter.com",
        "x.com",
        "www.x.com",
        "youtube.com",
        "www.youtube.com",
        "linkedin.com",
        "www.linkedin.com",
    }

    for domain in ignored_domains:

        if domain in url_lower:
            return False

    # =====================================================
    # HIGH-VALUE RENTAL PLATFORMS
    # =====================================================

    priority_domains = {
        "turbotenant.com",
        "rental.turbotenant.com",
        "renter.turbotenant.com",
    }

    for domain in priority_domains:

        if domain in url_lower:
            return True

    # =====================================================
    # RELEVANT URL KEYWORDS
    # =====================================================

    relevant_keywords = (
        "contact",
        "landlord",
        "property",
        "rental",
        "rent",
        "leasing",
        "lease",
        "management",
        "manager",
        "application",
        "apply",
        "housing",
        "apartments",
    )

    return any(
        keyword in url_lower
        for keyword in relevant_keywords
    )


# =========================================================
# EXTERNAL PHONE EXTRACTION
# =========================================================

def extract_phone_from_external_page(url):
    """
    Consulta UNA página externa utilizando requests.

    No utiliza Playwright para evitar crear más Chromium
    resources.

    Devuelve:
        phone, source
    """

    if not url:
        return None, None

    print(
        f"Checking external contact page: {url}"
    )

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=EXTERNAL_REQUEST_TIMEOUT,
            allow_redirects=True
        )

        print(
            f"External page status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "External page unavailable."
            )

            return None, None

        # =================================================
        # PHONE EXTRACTION
        # =================================================

        phone = extract_phone_from_html(
            response.text
        )

        if phone:

            print(
                "PHONE FOUND ON EXTERNAL PAGE:",
                phone
            )

            return phone, "external"

        print(
            "No phone found on external page."
        )

        return None, None

    except requests.RequestException as e:

        print(
            "External page request error:",
            repr(e)
        )

        return None, None

    except Exception as e:

        print(
            "External page processing error:",
            repr(e)
        )

        return None, None


# =========================================================
# FIND EXTERNAL CONTACT URLS
# =========================================================

def find_external_contact_urls(page, ad_url):
    """
    Obtiene los enlaces existentes dentro de la descripción
    de Craigslist y selecciona únicamente aquellos que
    parecen relevantes para contacto/rental/property management.

    No abre los enlaces aquí.
    """

    urls = []

    try:

        links = page.locator(
            "#postingbody a[href]"
        )

        count = links.count()

        print(
            f"External links found in description: {count}"
        )

        for i in range(count):

            try:

                href = links.nth(i).get_attribute(
                    "href"
                )

                if not href:
                    continue

                absolute_url = urljoin(
                    ad_url,
                    href
                )

                if is_relevant_external_url(
                    absolute_url
                ):

                    if absolute_url not in urls:

                        urls.append(
                            absolute_url
                        )

            except Exception:

                continue

    except Exception as e:

        print(
            "Error finding external URLs:",
            repr(e)
        )

    print(
        f"Relevant external URLs selected: {len(urls)}"
    )

    for url in urls:

        print(
            f"  External candidate: {url}"
        )

    return urls


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

    Busca teléfono mediante:

    1. tel: link
    2. descripción
    3. show contact info
    4. páginas externas relevantes

    La page de Playwright se reutiliza entre anuncios.
    """

    try:

        print(
            f"\nOpening: {url}"
        )

        # =====================================================
        # OPEN AD
        # =====================================================

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=20000
        )

        # =====================================================
        # POSTED DATE
        # =====================================================

        posted_at = None

        time_tag = page.locator(
            "time"
        ).first

        if time_tag.count() > 0:

            posted_at = time_tag.get_attribute(
                "datetime"
            )

        # =====================================================
        # INITIAL DESCRIPTION
        # =====================================================

        body = page.locator(
            "#postingbody"
        )

        description = ""

        if body.count() > 0:

            description = (
                body.inner_text()
                .strip()
            )

            prefix = (
                "QR Code Link to This Post"
            )

            if description.startswith(
                prefix
            ):

                description = (
                    description[
                        len(prefix):
                    ]
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

        contact_count = (
            contact_element.count()
        )

        print(
            f"Contact info elements found: "
            f"{contact_count}"
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

                page.wait_for_timeout(
                    3000
                )

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
                body.inner_text()
                .strip()
            )

        if not updated_description:

            updated_description = (
                description
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
        phone_source = None

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

                phone_source = "craigslist_tel"

                print(
                    "PHONE FOUND FROM TEL LINK:",
                    phone
                )

        # =====================================================
        # SECOND: PHONE INSIDE DESCRIPTION
        # =====================================================

        if not phone:

            phone = extract_phone_from_text(
                updated_description
            )

            if phone:

                phone_source = (
                    "craigslist_description"
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
        # THIRD: EXTERNAL LINKS
        # =====================================================

        if not phone:

            print(
                "No Craigslist phone found."
            )

            external_urls = (
                find_external_contact_urls(
                    page,
                    url
                )
            )

            checked_external_pages = 0

            for external_url in external_urls:

                if (
                    checked_external_pages
                    >= MAX_EXTERNAL_PAGES_PER_AD
                ):
                    break

                checked_external_pages += 1

                external_phone, source = (
                    extract_phone_from_external_page(
                        external_url
                    )
                )

                if external_phone:

                    phone = external_phone

                    phone_source = source

                    print(
                        "PHONE FOUND VIA EXTERNAL PAGE:",
                        phone
                    )

                    break

        # =====================================================
        # NO PHONE
        # =====================================================

        if not phone:

            print(
                "FINAL RESULT: No phone found."
            )

        else:

            print(
                f"FINAL PHONE: {phone}"
            )

            print(
                f"PHONE SOURCE: {phone_source}"
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
            "phone": phone,
            "phone_source": phone_source,
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
            "phone": None,
            "phone_source": None,
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

def scan(already_processed=None):
    """
    Busca MAX_ADS anuncios NUEVOS empezando desde
    el offset actual.

    Los anuncios que ya existen en la base de datos
    se saltan automáticamente.
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

    # =====================================================
    # REQUEST SEARCH PAGE
    # =====================================================

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
        f"Listings found on search page: "
        f"{len(listings)}"
    )

    if not listings:

        print(
            "No listings found."
        )

        return []

    ads = []

    # =====================================================
    # PLAYWRIGHT
    # =====================================================

    with sync_playwright() as p:

        browser = None
        context = None
        page = None

        try:

            # =================================================
            # LAUNCH LIGHTER CHROMIUM
            # =================================================

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-sandbox"
                ]
            )

            # =================================================
            # CREATE EXPLICIT CONTEXT
            # =================================================

            context = browser.new_context(
                user_agent=HEADERS[
                    "User-Agent"
                ]
            )

            # =================================================
            # CREATE SINGLE REUSABLE PAGE
            # =================================================

            page = context.new_page()

            # =================================================
            # BLOCK UNNECESSARY RESOURCES
            # =================================================

            def handle_route(route):

                resource_type = (
                    route.request.resource_type
                )

                if resource_type in {
                    "image",
                    "font",
                    "media"
                }:

                    route.abort()

                else:

                    route.continue_()

            page.route(
                "**/*",
                handle_route
            )

            # =================================================
            # PROCESS LISTINGS
            # =================================================

            for listing in listings:

                # =============================================
                # STOP WHEN WE HAVE ENOUGH NEW ADS
                # =============================================

                if len(ads) >= MAX_ADS:

                    break

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
                # SKIP ALREADY PROCESSED ADS
                # =============================================

                if already_processed:

                    try:

                        if already_processed(
                            ad_url
                        ):

                            print(
                                "Already processed, "
                                f"skipping: {ad_url}"
                            )

                            continue

                    except Exception as e:

                        print(
                            "Error checking "
                            "already_processed:",
                            repr(e)
                        )

                        # If database check fails,
                        # skip this ad for safety.

                        continue

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

                if detail.get(
                    "phone_source"
                ):

                    print(
                        "Phone source:",
                        detail[
                            "phone_source"
                        ]
                    )

                # =============================================
                # DELAY
                # =============================================

                time.sleep(
                    DELAY_BETWEEN_REQUESTS
                )

        finally:

            # =================================================
            # EXPLICIT RESOURCE CLEANUP
            # =================================================

            if page is not None:

                try:

                    page.close()

                except Exception:

                    pass

            if context is not None:

                try:

                    context.close()

                except Exception:

                    pass

            if browser is not None:

                try:

                    browser.close()

                except Exception:

                    pass

    # =====================================================
    # SAVE NEXT OFFSET
    # =====================================================

    next_offset = (
        offset + MAX_ADS
    )

    save_offset(
        next_offset
    )

    print(
        f"Craigslist offset updated: "
        f"{offset} -> {next_offset}"
    )

    print(
        f"Craigslist scan completed. "
        f"{len(ads)} new ads processed."
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
