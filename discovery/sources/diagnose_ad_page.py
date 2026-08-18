import requests
from bs4 import BeautifulSoup

# Usa aquí una URL real de las que ya extrajo tu scan() en la última ejecución
AD_URL = "https://www.craigslist.org/view/d/san-diego-everything-has-been-updated/2xweHBsfXzqzh61NzkkzXR"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


def diagnose():

    response = requests.get(
        AD_URL,
        headers=HEADERS,
        timeout=10
    )

    print("Status code:", response.status_code)

    soup = BeautifulSoup(response.text, "html.parser")

    body = soup.find(id="postingbody")

    print("¿Existe #postingbody?:", body is not None)

    if body:
        print("\n--- Texto completo de la descripción ---")
        print(body.get_text(strip=True))

    time_tag = soup.find("time")

    print("\n¿Existe <time> en la página del anuncio?:", time_tag is not None)

    if time_tag:
        print("Atributos del <time>:", time_tag.attrs)


if __name__ == "__main__":

    diagnose()