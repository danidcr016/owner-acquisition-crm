from discovery_scoring import calculate_score
from discovery.sources import test_source, craigslist_source


# =========================================================
# SCAN
# =========================================================

def scan(already_processed=None):

    return craigslist_source.scan(
        already_processed=already_processed
    )


# =========================================================
# ANALYZE
# =========================================================

def analyze(ad):

    title = ad.get(
        "title",
        ""
    )

    original_description = ad.get(
        "description",
        ""
    )

    description = (
        f"{title}. {original_description}"
    )

    return {

        "title": title,

        "description": description,

        "city": ad.get(
            "city",
            ""
        ),

        "source": ad.get(
            "source",
            ""
        ),

        "url": ad.get(
            "url",
            ""
        ),

        "phone": ad.get(
            "phone"
        )

    }


# =========================================================
# SCORE
# =========================================================

def score(ad):

    ad["score"] = calculate_score(
        ad.get(
            "description",
            ""
        )
    )

    return ad


# =========================================================
# PROCESS ADS
# =========================================================

def process_ads(
    ads,
    save_function
):

    saved_ads = []

    for ad in ads:

        analyzed_ad = analyze(
            ad
        )

        scored_ad = score(
            analyzed_ad
        )

        saved_ad = save_function(

            title=scored_ad["title"],

            description=scored_ad["description"],

            city=scored_ad["city"],

            source=scored_ad["source"],

            url=scored_ad["url"],

            phone=scored_ad.get(
                "phone"
            ),

            score=scored_ad["score"]

        )

        saved_ads.append(
            saved_ad
        )

    return saved_ads


# =========================================================
# DIRECT EXECUTION
# =========================================================

if __name__ == "__main__":

    ads = scan()

    for ad in ads:

        analyzed_ad = analyze(
            ad
        )

        scored_ad = score(
            analyzed_ad
        )

        print(
            scored_ad
        )