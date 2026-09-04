from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum

from projects.models import FeedbackClusterVote, FeedbackCycle


VOTE_BUDGET = 3


def voter_label(user) -> str:
    full_name = user.get_full_name().strip()
    return full_name or user.get_username()


def eligible_voters_for(cycle):
    return (
        get_user_model()
        .objects.filter(is_active=True, project_memberships__project=cycle.project)
        .distinct()
        .order_by("username", "id")
    )


def saved_vote_allocation_for(cycle, voter) -> dict[int, int]:
    allocations = {
        vote.cluster_id: vote.vote_count
        for vote in FeedbackClusterVote.objects.filter(cycle=cycle, voter=voter)
    }
    return {
        cluster_id: allocations.get(cluster_id, 0)
        for cluster_id in cycle.feedback_clusters.values_list("id", flat=True)
    }


def completed_voter_ids_for(cycle) -> set[int]:
    totals = (
        FeedbackClusterVote.objects.filter(cycle=cycle)
        .values("voter_id")
        .annotate(total=Sum("vote_count"))
    )
    return {row["voter_id"] for row in totals if row["total"] == VOTE_BUDGET}


def voting_progress_for(cycle):
    completed_voter_ids = completed_voter_ids_for(cycle)
    return [
        {
            "id": voter.pk,
            "label": voter_label(voter),
            "has_voted": voter.pk in completed_voter_ids,
        }
        for voter in eligible_voters_for(cycle)
    ]


def every_eligible_voter_has_voted(cycle) -> bool:
    eligible_voter_ids = set(eligible_voters_for(cycle).values_list("id", flat=True))
    return bool(eligible_voter_ids) and eligible_voter_ids <= completed_voter_ids_for(cycle)


def open_voting(cycle):
    if (
        cycle.status != FeedbackCycle.Status.RETROSPECTIVE
        or cycle.voting_status != FeedbackCycle.VotingStatus.CLUSTERING
        or not cycle.feedback_clusters.exists()
    ):
        return False

    cycle.voting_status = FeedbackCycle.VotingStatus.OPEN
    cycle.save(update_fields=["voting_status", "updated_at"])
    return True


def close_voting(cycle):
    if (
        cycle.status != FeedbackCycle.Status.RETROSPECTIVE
        or cycle.voting_status != FeedbackCycle.VotingStatus.OPEN
    ):
        return False

    cycle.voting_status = FeedbackCycle.VotingStatus.CLOSED
    cycle.save(update_fields=["voting_status", "updated_at"])
    return True


@transaction.atomic
def save_vote_allocation(cycle, voter, allocations):
    if (
        cycle.status != FeedbackCycle.Status.RETROSPECTIVE
        or cycle.voting_status != FeedbackCycle.VotingStatus.OPEN
        or not cycle.feedback_clusters.exists()
    ):
        return False

    for cluster, vote_count in allocations.items():
        FeedbackClusterVote.objects.update_or_create(
            cycle=cycle,
            voter=voter,
            cluster=cluster,
            defaults={"vote_count": vote_count},
        )

    if every_eligible_voter_has_voted(cycle):
        cycle.voting_status = FeedbackCycle.VotingStatus.CLOSED
        cycle.save(update_fields=["voting_status", "updated_at"])

    return True


def ranked_clusters_for(cycle):
    vote_totals = {
        row["cluster_id"]: row["total"] or 0
        for row in FeedbackClusterVote.objects.filter(cycle=cycle)
        .values("cluster_id")
        .annotate(total=Sum("vote_count"))
    }
    clusters = []
    for cluster in cycle.feedback_clusters.all():
        clusters.append(
            {
                "id": cluster.pk,
                "name": cluster.name,
                "object": cluster,
                "vote_total": vote_totals.get(cluster.pk, 0),
                "created_at": cluster.created_at,
            }
        )

    return sorted(
        clusters,
        key=lambda cluster: (
            -cluster["vote_total"],
            cluster["created_at"],
            cluster["id"],
        ),
    )
