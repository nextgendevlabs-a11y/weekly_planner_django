from django.db.models import Exists, OuterRef

from projects.models import FeedbackCard, Membership


def submission_progress_for(cycle):
    submitted_cards = FeedbackCard.objects.filter(
        cycle=cycle,
        author=OuterRef("user_id"),
    )
    memberships = (
        Membership.objects.filter(project=cycle.project, user__is_active=True)
        .select_related("user")
        .annotate(has_submitted_feedback=Exists(submitted_cards))
        .order_by("user__username", "user__id")
    )

    return [
        {
            "user_label": membership.user.get_username(),
            "has_submitted_feedback": membership.has_submitted_feedback,
        }
        for membership in memberships
    ]
