POSITIVE_KEYWORDS = {
    "furnished": 30,
    "short-term": 40,
    "monthly": 30,
    "month-to-month": 30
}


NEGATIVE_KEYWORDS = {
    "12 month lease": -50,
    "lease length: one year": -50,
    "lease term: one year": -50,
    "unfurnished": -25
}


def calculate_score(description):

    score = 0

    description = description.lower()

    # Evitar que "furnished patio" cuente como amueblado
    cleaned_description = description.replace("furnished patio", "")

    for keyword, points in POSITIVE_KEYWORDS.items():

        if keyword in cleaned_description:
            score += points

    for keyword, points in NEGATIVE_KEYWORDS.items():

        if keyword in description:
            score += points

    score = max(0, min(score, 100))

    return score


if __name__ == "__main__":

    test_description = (
        "Furnished apartment. "
        "Monthly rental. "
        "Utilities included."
    )

    score = calculate_score(test_description)

    print(f"Test score: {score}")