from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from projects.models import FeedbackCard
from projects.retrospective_board import revealed_feedback_cards_for


@dataclass(frozen=True)
class ClusterSuggestion:
    name: str
    card_ids: tuple[int, ...]


class FeedbackClusteringService(Protocol):
    def suggest_clusters(self, cycle) -> list[ClusterSuggestion]:
        """Return draft cluster suggestions for revealed cards in one cycle."""


class LocalDeterministicClusteringService:
    """A stable local baseline that groups revealed cards by feedback category."""

    def suggest_clusters(self, cycle) -> list[ClusterSuggestion]:
        cards = revealed_feedback_cards_for(cycle)
        if not cards:
            return []

        suggestions = []
        for category_value, category_label in FeedbackCard.Category.choices:
            card_ids = tuple(
                card["id"] for card in cards if card["category"] == category_value
            )
            if card_ids:
                suggestions.append(
                    ClusterSuggestion(
                        name=f"{category_label} themes",
                        card_ids=card_ids,
                    )
                )
        return suggestions


def get_clustering_service() -> FeedbackClusteringService:
    service_path = getattr(settings, "PROJECTS_CLUSTERING_SERVICE", "")
    if not service_path:
        return LocalDeterministicClusteringService()

    service_factory = import_string(service_path)
    service = service_factory() if isinstance(service_factory, type) else service_factory
    if not hasattr(service, "suggest_clusters"):
        raise ImproperlyConfigured(
            "PROJECTS_CLUSTERING_SERVICE must provide suggest_clusters(cycle)."
        )
    return service


def draft_from_suggestions(suggestions: list[ClusterSuggestion]) -> dict:
    return {
        "clusters": [
            {
                "name": suggestion.name,
                "card_ids": list(suggestion.card_ids),
            }
            for suggestion in suggestions
        ]
    }
