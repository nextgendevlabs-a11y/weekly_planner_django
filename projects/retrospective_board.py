from projects.models import FeedbackCard


def _author_label(card: FeedbackCard) -> str:
    full_name = card.author.get_full_name().strip()
    return full_name or card.author.get_username()


def retrospective_board_sections_for(cycle):
    sections = {
        category_value: {
            "value": category_value,
            "label": category_label,
            "cards": [],
        }
        for category_value, category_label in FeedbackCard.Category.choices
    }
    cards = []

    for row in cycle.feedback_cards.filter(is_anonymous=True).values(
        "category",
        "text",
        "is_anonymous",
        "created_at",
        "id",
    ):
        cards.append(
            {
                "sort_key": (row["created_at"], row["id"]),
                "category": row["category"],
                "text": row["text"],
                "is_anonymous": row["is_anonymous"],
                "author_label": "Anonymous contributor",
            }
        )

    for card in cycle.feedback_cards.filter(is_anonymous=False).select_related("author"):
        cards.append(
            {
                "sort_key": (card.created_at, card.id),
                "category": card.category,
                "text": card.text,
                "is_anonymous": card.is_anonymous,
                "author_label": _author_label(card),
            }
        )

    for card in sorted(cards, key=lambda item: item["sort_key"]):
        sections[card["category"]]["cards"].append(
            {
                "text": card["text"],
                "is_anonymous": card["is_anonymous"],
                "author_label": card["author_label"],
            }
        )

    return list(sections.values())
