from discovery_scoring import calculate_score


def scan():

    return [

        {
            "title": "Apartment Downtown",
            "description": (
                "Furnished apartment. "
                "Monthly rental available. "
                "Utilities included."
            ),
            "city": "San Diego",
            "source": "Test",
            "url": "https://example.com/apartment-downtown"
        },

        {
            "title": "Mission Valley Condo",
            "description": (
                "12 month lease only. "
                "Unfurnished."
            ),
            "city": "San Diego",
            "source": "Test",
            "url": "https://example.com/mission-valley"
        },

        {
            "title": "Pacific Beach Apartment",
            "description": (
                "Fully furnished apartment. "
                "Monthly rental available. "
                "Utilities included."
            ),
            "city": "San Diego",
            "source": "Test",
            "url": "https://example.com/pacific-beach"
        }

    ]


def analyze(ad):

    description = ad.get(
        "description",
        ""
    )

    return {
        "title": ad.get("title", ""),
        "description": description,
        "city": ad.get("city", ""),
        "source": ad.get("source", ""),
        "url": ad.get("url", "")
    }


def score(ad):

    ad["score"] = calculate_score(
        ad.get("description", "")
    )

    return ad

def process_ads(ads, save_function):

    saved_ads = []

    for ad in ads:

        analyzed_ad = analyze(ad)

        scored_ad = score(analyzed_ad)

        saved_ad = save_function(
            title=scored_ad["title"],
            description=scored_ad["description"],
            city=scored_ad["city"],
            source=scored_ad["source"],
            url=scored_ad["url"],
            score=scored_ad["score"]
        )

        saved_ads.append(saved_ad)

    return saved_ads


if __name__ == "__main__":

    ads = scan()

    for ad in ads:

        analyzed_ad = analyze(ad)

        scored_ad = score(analyzed_ad)

        print(scored_ad)

  