import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://sandiego.craigslist.org/search/apa?query=furnished"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


# Límite de anuncios a procesar en esta fase de prueba
MAX_ADS = 40

# Segundos de espera entre cada petición a un anuncio individual
DELAY_BETWEEN_REQUESTS = 1.5


def get_ad_detail(url):
    """
    Accede al anuncio individual de Craigslist
    y extrae descripción y fecha de publicación.
    """

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=10
    )

    if response.status_code != 200:
        return {
            "description": "",
            "posted_at": None
        }

    soup = BeautifulSoup(response.text, "html.parser")

    # =========================
    # DESCRIPCIÓN
    # =========================

    body = soup.find(id="postingbody")

    description = ""

    if body:
        description = body.get_text(" ", strip=True)

        # Craigslist añade este texto fijo al principio
        prefix = "QR Code Link to This Post"

        if description.startswith(prefix):
            description = description[len(prefix):].strip()

    # =========================
    # FECHA DE PUBLICACIÓN
    # =========================

    time_tag = soup.find("time")

    posted_at = (
        time_tag.get("datetime")
        if time_tag
        else None
    )

    return {
        "description": description,
        "posted_at": posted_at
    }


def scan():
    """
    Busca anuncios en Craigslist y devuelve
    una lista de anuncios preparados para Discovery.
    """

    response = requests.get(
        BASE_URL,
        headers=HEADERS,
        timeout=10
    )

    if response.status_code != 200:
        print(
            "Craigslist devolvió status:",
            response.status_code
        )

        return []

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # =========================
    # RESULTADOS
    # =========================

    listings = soup.select(
        "li.cl-static-search-result"
    )

    ads = []

    for listing in listings[:MAX_ADS]:

        # =========================
        # URL REAL DEL ANUNCIO
        # =========================

        link_tag = listing.find(
            "a",
            href=True
        )

        if link_tag is None:
            continue

        # Convierte URLs relativas en URLs absolutas
        url = urljoin(
            BASE_URL,
            link_tag["href"]
        )

        # =========================
        # TÍTULO
        # =========================

        title_div = listing.find(
            "div",
            class_="title"
        )

        title = (
            title_div.get_text(strip=True)
            if title_div
            else ""
        )

        # =========================
        # CIUDAD / LOCALIZACIÓN
        # =========================

        location_div = listing.find(
            "div",
            class_="location"
        )

        city = (
            location_div.get_text(strip=True)
            if location_div
            else "San Diego"
        )

        # =========================
        # DETALLE DEL ANUNCIO
        # =========================

        detail = get_ad_detail(url)

        # =========================
        # ANUNCIO FINAL
        # =========================

        ad = {
            "title": title,
            "description": (
                detail["description"]
                or title
            ),
            "city": city,
            "source": "Craigslist",
            "url": url,
            "posted_at": detail["posted_at"]
        }

        ads.append(ad)

        # Esperamos antes de pedir el siguiente anuncio
        time.sleep(
            DELAY_BETWEEN_REQUESTS
        )

    return ads


if __name__ == "__main__":

    ads = scan()

    print(
        "Anuncios extraídos:",
        len(ads)
    )

    for ad in ads:
        print(ad)