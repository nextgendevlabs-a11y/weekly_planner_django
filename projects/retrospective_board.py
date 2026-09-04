from projects.models import FeedbackCard


def _author_label(card: FeedbackCard) -> str:
    full_name = card.author.get_full_name().strip()
    return full_name or card.author.get_username()


def revealed_feedback_cards_for(cycle):
    cards = []

    for row in cycle.feedback_cards.filter(is_anonymous=True).values(
        "category",
        "cluster_id",
        "text",
        "is_anonymous",
        "created_at",
        "id",
    ):
        cards.append(
            {
                "sort_key": (row["created_at"], row["id"]),
                "id": row["id"],
                "category": row["category"],
                "category_label": FeedbackCard.Category(row["category"]).label,
                "cluster_id": row["cluster_id"],
                "text": row["text"],
                "is_anonymous": row["is_anonymous"],
                "author_label": "Anonymous contributor",
            }
        )

    for card in cycle.feedback_cards.filter(is_anonymous=False).select_related("author"):
        cards.append(
            {
                "sort_key": (card.created_at, card.id),
                "id": card.id,
                "category": card.category,
                "category_label": card.get_category_display(),
                "cluster_id": card.cluster_id,
                "text": card.text,
                "is_anonymous": card.is_anonymous,
                "author_label": _author_label(card),
            }
        )

    return sorted(cards, key=lambda item: item["sort_key"])


def retrospective_board_context_for(cycle):
    sections = {
        category_value: {
            "value": category_value,
            "label": category_label,
            "cards": [],
        }
        for category_value, category_label in FeedbackCard.Category.choices
    }
    clusters = [
        {
            "id": cluster.id,
            "name": cluster.name,
            "object": cluster,
            "cards": [],
        }
        for cluster in cycle.feedback_clusters.all()
    ]
    clusters_by_id = {cluster["id"]: cluster for cluster in clusters}

    for card in revealed_feedback_cards_for(cycle):
        if card["cluster_id"] in clusters_by_id:
            clusters_by_id[card["cluster_id"]]["cards"].append(card)
        else:
            sections[card["category"]]["cards"].append(card)

    return {
        "clusters": clusters,
        "ungrouped_sections": list(sections.values()),
    }


def retrospective_board_sections_for(cycle):
    return retrospective_board_context_for(cycle)["ungrouped_sections"]
