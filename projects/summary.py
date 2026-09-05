from django.db.models import Sum

from projects.models import ActionItem, FeedbackCard
from projects.retrospective_board import revealed_feedback_cards_for
from projects.voting import VOTE_BUDGET, ranked_clusters_for, voter_label


def completed_voter_count_for(cycle) -> int:
    return (
        cycle.cluster_votes.values("voter_id")
        .annotate(total=Sum("vote_count"))
        .filter(total=VOTE_BUDGET)
        .count()
    )


def submitted_member_count_for(cycle) -> int:
    return cycle.feedback_cards.values("author_id").distinct().count()


def summary_topics_for(cycle):
    topics = ranked_clusters_for(cycle)
    for topic in topics:
        cluster = topic["object"]
        topic["discussion_status_label"] = cluster.get_discussion_status_display()
        topic["discussion_notes"] = cluster.discussion_notes
    return topics


def summary_feedback_cards_for(cycle):
    topic_names_by_id = {
        cluster_id: name
        for cluster_id, name in cycle.feedback_clusters.values_list("id", "name")
    }
    cards = []
    for card in revealed_feedback_cards_for(cycle):
        card = card.copy()
        card["topic_name"] = topic_names_by_id.get(card["cluster_id"])
        cards.append(card)
    return cards


def summary_attendees_for(cycle):
    return [
        {"id": attendance.user_id, "label": voter_label(attendance.user)}
        for attendance in cycle.attendance_records.select_related("user").order_by(
            "user__username",
            "user_id",
        )
    ]


def retrospective_summary_context_for(cycle):
    return {
        "approved_summary_text": cycle.approved_retrospective_summary_text,
        "topics": summary_topics_for(cycle),
        "decisions": list(
            cycle.decisions.select_related("topic").order_by("created_at", "id")
        ),
        "action_items": list(
            cycle.action_items.select_related("owner", "topic").order_by(
                "due_date",
                "created_at",
                "id",
            )
        ),
        "attendees": summary_attendees_for(cycle),
        "active_member_count_at_publication": cycle.summary_active_member_count,
        "submitted_member_count": submitted_member_count_for(cycle),
        "completed_voter_count": completed_voter_count_for(cycle),
        "feedback_cards": summary_feedback_cards_for(cycle),
        "feedback_categories": FeedbackCard.Category,
        "action_statuses": ActionItem.Status,
    }
