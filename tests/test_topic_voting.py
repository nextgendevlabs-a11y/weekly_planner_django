from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from projects.forms import FeedbackClusterVoteForm
from projects.models import (
    FeedbackCard,
    FeedbackCluster,
    FeedbackClusterVote,
    FeedbackCycle,
    Membership,
    Project,
)
from projects.voting import ranked_clusters_for, saved_vote_allocation_for


pytestmark = pytest.mark.django_db


class BoardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and "action" in attributes:
            self.forms.append(
                {
                    "action": attributes["action"],
                    "method": attributes.get("method", "get").lower(),
                }
            )


def create_user(
    username="member",
    *,
    password="UsablePass123!",
    is_active=True,
    is_staff=False,
    is_superuser=False,
    first_name="",
    last_name="",
    email="",
):
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
        first_name=first_name,
        last_name=last_name,
        email=email,
    )


def create_project(name="Weekly Ops"):
    return Project.objects.create(name=name)


def add_membership(user, project, role=Membership.Role.TEAM_MEMBER):
    return Membership.objects.create(user=user, project=project, role=role)


def create_cycle(
    project,
    facilitator,
    *,
    label="Week 34 Retrospective",
    status=FeedbackCycle.Status.RETROSPECTIVE,
    voting_status=FeedbackCycle.VotingStatus.CLUSTERING,
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        voting_status=voting_status,
        opens_at=timezone.now(),
    )


def create_cluster(cycle, name="Release readiness"):
    return FeedbackCluster.objects.create(cycle=cycle, name=name)


def create_card(cycle, author, *, text="Retrospective feedback", cluster=None):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=FeedbackCard.Category.START,
        text=text,
        cluster=cluster,
    )


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def voting_open_path(project, cycle):
    return reverse(
        "feedback_cycle_voting_open",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def voting_submit_path(project, cycle):
    return reverse(
        "feedback_cycle_vote_submit",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def voting_close_path(project, cycle):
    return reverse(
        "feedback_cycle_voting_close",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def cluster_create_path(project, cycle):
    return reverse(
        "feedback_cluster_create",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def cluster_rename_path(project, cycle, cluster):
    return reverse(
        "feedback_cluster_rename",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "cluster_id": cluster.pk,
        },
    )


def card_move_path(project, cycle, card):
    return reverse(
        "feedback_card_move",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "card_id": card.pk,
        },
    )


def cluster_merge_path(project, cycle, cluster):
    return reverse(
        "feedback_cluster_merge",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "cluster_id": cluster.pk,
        },
    )


def cluster_split_path(project, cycle, cluster):
    return reverse(
        "feedback_cluster_split",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "cluster_id": cluster.pk,
        },
    )


def suggestions_accept_path(project, cycle):
    return reverse(
        "feedback_cluster_suggestions_accept",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def vote_data(allocations):
    return {
        f"cluster_{cluster.pk}_votes": str(vote_count)
        for cluster, vote_count in allocations.items()
    }


def parser_from(response):
    parser = BoardParser()
    parser.feed(response.content.decode())
    return parser


def assert_no_secret_leak(response, secrets):
    content = response.content.decode()
    for secret in secrets:
        assert secret not in content


def test_feedback_cycle_tracks_voting_status_and_vote_model_scopes_allocations():
    voter = create_user(username="voter")
    facilitator = create_user(username="facilitator")
    project = create_project("Voting Model Project")
    cycle = create_cycle(project, facilitator)
    other_cycle = create_cycle(
        project,
        facilitator,
        label="Completed week",
        status=FeedbackCycle.Status.COMPLETED,
    )
    cluster = create_cluster(cycle, "Planning")
    other_cluster = create_cluster(other_cycle, "Other planning")

    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLUSTERING
    assert cycle.is_voting_open is False
    assert cycle.is_voting_closed is False

    vote = FeedbackClusterVote.objects.create(
        cycle=cycle,
        voter=voter,
        cluster=cluster,
        vote_count=3,
    )
    assert vote.cycle == cycle
    assert vote.voter == voter
    assert vote.cluster == cluster
    assert str(vote) == f"{voter} vote for {cluster}"

    with pytest.raises(IntegrityError), transaction.atomic():
        FeedbackClusterVote.objects.create(
            cycle=cycle,
            voter=voter,
            cluster=cluster,
            vote_count=1,
        )

    invalid_vote = FeedbackClusterVote(
        cycle=cycle,
        voter=voter,
        cluster=other_cluster,
        vote_count=1,
    )
    with pytest.raises(ValidationError) as error:
        invalid_vote.full_clean()
    assert "same feedback cycle" in error.value.message_dict["cluster"][0]

    with pytest.raises(IntegrityError), transaction.atomic():
        FeedbackClusterVote.objects.create(
            cycle=cycle,
            voter=voter,
            cluster=cluster,
            vote_count=4,
        )


def test_open_voting_is_facilitator_only_retrospective_only_and_requires_cluster(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Open Voting Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Open Voting Week")

    client.force_login(member)
    member_response = client.post(voting_open_path(project, cycle))
    assert member_response.status_code == 404

    client.force_login(facilitator)
    no_cluster_response = client.post(voting_open_path(project, cycle))
    assert no_cluster_response.status_code == 404
    cycle.refresh_from_db()
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLUSTERING
    assert FeedbackClusterVote.objects.count() == 0

    cluster = create_cluster(cycle, "Customer focus")
    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    assert "Open voting" in content
    assert {"action": voting_open_path(project, cycle), "method": "post"} in (
        parser_from(board_response).forms
    )

    open_response = client.post(voting_open_path(project, cycle))
    assert open_response.status_code == 302
    cycle.refresh_from_db()
    assert cycle.voting_status == FeedbackCycle.VotingStatus.OPEN

    collecting_cycle = create_cycle(
        create_project("Collecting Voting Project"),
        facilitator,
        status=FeedbackCycle.Status.COLLECTING_FEEDBACK,
    )
    create_cluster(collecting_cycle)
    collecting_response = client.post(voting_open_path(collecting_cycle.project, collecting_cycle))
    assert collecting_response.status_code == 404
    assert cluster.name == "Customer focus"


def test_board_exposes_voting_only_for_revealed_cycle_and_never_for_ungrouped_cards(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project("Visible Voting Project")
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.OPEN)
    cluster = create_cluster(cycle, "Grouped only")
    clustered_card = create_card(cycle, member, text="Clustered feedback", cluster=cluster)
    ungrouped_card = create_card(cycle, member, text="Ungrouped feedback")
    client.force_login(member)

    response = client.get(board_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Save votes" in content
    assert f'name="cluster_{cluster.pk}_votes"' in content
    assert f"cluster_{ungrouped_card.pk}_votes" not in content
    assert "Ungrouped feedback" in content
    assert clustered_card.text in content


@pytest.mark.parametrize(
    "data",
    [
        "missing",
        "zero",
        "one",
        "two",
        "four",
        "negative",
        "decimal",
        "text",
    ],
)
def test_vote_validation_rejects_invalid_totals_or_values_without_changing_saved_votes(
    client,
    data,
):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project("Validation Voting Project")
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.OPEN)
    first = create_cluster(cycle, "First")
    second = create_cluster(cycle, "Second")
    FeedbackClusterVote.objects.create(cycle=cycle, voter=member, cluster=first, vote_count=2)
    FeedbackClusterVote.objects.create(cycle=cycle, voter=member, cluster=second, vote_count=1)
    invalid_payloads = {
        "zero": vote_data({first: 0, second: 0}),
        "one": vote_data({first: 1, second: 0}),
        "two": vote_data({first: 1, second: 1}),
        "four": vote_data({first: 3, second: 1}),
        "negative": vote_data({first: -1, second: 4}),
        "decimal": {
            f"cluster_{first.pk}_votes": "1.5",
            f"cluster_{second.pk}_votes": "1.5",
        },
        "text": {
            f"cluster_{first.pk}_votes": "two",
            f"cluster_{second.pk}_votes": "1",
        },
    }
    post_data = invalid_payloads.get(data, {})
    client.force_login(member)

    response = client.post(voting_submit_path(project, cycle), post_data)

    assert response.status_code == 200
    assert saved_vote_allocation_for(cycle, member) == {first.pk: 2, second.pk: 1}
    cycle.refresh_from_db()
    assert cycle.voting_status == FeedbackCycle.VotingStatus.OPEN


def test_vote_form_requires_current_cycle_cluster_fields_and_exactly_three_votes():
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    first = create_cluster(cycle, "First")
    second = create_cluster(cycle, "Second")

    valid_form = FeedbackClusterVoteForm(
        vote_data({first: 2, second: 1}),
        cycle=cycle,
    )
    assert valid_form.is_valid() is True

    missing_form = FeedbackClusterVoteForm(
        {f"cluster_{first.pk}_votes": "3"},
        cycle=cycle,
    )
    assert missing_form.is_valid() is False
    assert "Enter a vote count for every cluster." in str(missing_form.errors)

    short_form = FeedbackClusterVoteForm(
        vote_data({first: 1, second: 1}),
        cycle=cycle,
    )
    assert short_form.is_valid() is False
    assert "Allocate exactly three votes." in str(short_form.errors)


def test_members_can_stack_votes_update_only_their_own_allocation_and_use_separate_cycle_budgets(client):
    first_member = create_user(username="first-member")
    second_member = create_user(username="second-member")
    facilitator = create_user(username="facilitator")
    project = create_project("Stacked Voting Project")
    add_membership(first_member, project)
    add_membership(second_member, project)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.OPEN)
    other_cycle = create_cycle(
        project,
        facilitator,
        label="Closed prior week",
        status=FeedbackCycle.Status.COMPLETED,
        voting_status=FeedbackCycle.VotingStatus.OPEN,
    )
    first = create_cluster(cycle, "First")
    second = create_cluster(cycle, "Second")
    other_cluster = create_cluster(other_cycle, "Other")
    client.force_login(first_member)

    response = client.post(
        voting_submit_path(project, cycle),
        {
            **vote_data({first: 3, second: 0}),
            "voter": str(second_member.pk),
        },
    )
    assert response.status_code == 302
    assert saved_vote_allocation_for(cycle, first_member) == {first.pk: 3, second.pk: 0}
    assert saved_vote_allocation_for(cycle, second_member) == {first.pk: 0, second.pk: 0}

    update_response = client.post(
        voting_submit_path(project, cycle),
        vote_data({first: 1, second: 2}),
    )
    assert update_response.status_code == 302
    assert saved_vote_allocation_for(cycle, first_member) == {first.pk: 1, second.pk: 2}

    FeedbackClusterVote.objects.create(
        cycle=other_cycle,
        voter=first_member,
        cluster=other_cluster,
        vote_count=3,
    )
    assert saved_vote_allocation_for(other_cycle, first_member) == {other_cluster.pk: 3}


def test_one_member_one_cluster_all_three_votes_auto_closes_with_three_vote_result(client):
    member = create_user(username="solo")
    facilitator = create_user(username="facilitator")
    project = create_project("Solo Voting Project")
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.OPEN)
    cluster = create_cluster(cycle, "Only topic")
    client.force_login(member)

    response = client.post(voting_submit_path(project, cycle), vote_data({cluster: 3}))

    assert response.status_code == 302
    cycle.refresh_from_db()
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED
    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    assert "Only topic" in content
    assert "3 votes" in content
    assert "Save votes" not in content


def test_cross_cycle_or_project_cluster_vote_submission_is_rejected_without_leakage(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project("Secret Vote Project")
    other_project = create_project("Other Vote Project")
    add_membership(member, project)
    add_membership(member, other_project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Secret Vote Week",
        voting_status=FeedbackCycle.VotingStatus.OPEN,
    )
    other_cycle = create_cycle(
        other_project,
        facilitator,
        label="Other Vote Week",
        voting_status=FeedbackCycle.VotingStatus.OPEN,
    )
    cluster = create_cluster(cycle, "Secret cluster")
    other_cluster = create_cluster(other_cycle, "Other cluster")
    create_card(cycle, member, text="Secret card text", cluster=cluster)
    client.force_login(member)

    response = client.post(
        voting_submit_path(project, cycle),
        {
            f"cluster_{cluster.pk}_votes": "2",
            f"cluster_{other_cluster.pk}_votes": "1",
        },
    )

    assert response.status_code == 404
    assert_no_secret_leak(
        response,
        [
            "Secret Vote Project",
            "Secret Vote Week",
            "Secret cluster",
            "Secret card text",
            "Other Vote Project",
            "Other Vote Week",
            "Other cluster",
        ],
    )
    assert FeedbackClusterVote.objects.count() == 0


def test_open_voting_hides_aggregate_totals_and_shows_only_facilitator_progress(client):
    facilitator = create_user(username="facilitator", first_name="Frances", last_name="Lead")
    member = create_user(username="member", first_name="Mira", last_name="Member")
    other_member = create_user(username="other-member", first_name="Owen", last_name="Other")
    project = create_project("Private Voting Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(other_member, project)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.OPEN)
    first = create_cluster(cycle, "First")
    second = create_cluster(cycle, "Second")
    FeedbackClusterVote.objects.create(cycle=cycle, voter=member, cluster=first, vote_count=3)
    FeedbackClusterVote.objects.create(cycle=cycle, voter=member, cluster=second, vote_count=0)

    client.force_login(other_member)
    member_response = client.get(board_path(project, cycle))
    member_content = member_response.content.decode()
    assert "3 votes" not in member_content
    assert "Ranked discussion agenda" not in member_content
    assert "Voting progress" not in member_content
    assert "Mira Member" not in member_content
    assert f'name="cluster_{first.pk}_votes"' in member_content

    client.force_login(facilitator)
    facilitator_response = client.get(board_path(project, cycle))
    facilitator_content = facilitator_response.content.decode()
    assert "Voting progress" in facilitator_content
    assert "Mira Member" in facilitator_content
    assert "Owen Other" in facilitator_content
    assert "Voted" in facilitator_content
    assert "Waiting" in facilitator_content
    assert "3 votes" not in facilitator_content
    assert "Ranked discussion agenda" not in facilitator_content


def test_voting_auto_closes_after_all_current_active_members_vote_and_ignores_inactive_members(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    inactive = create_user(username="inactive", is_active=False)
    project = create_project("Auto Close Voting Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive, project)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.OPEN)
    first = create_cluster(cycle, "First")
    second = create_cluster(cycle, "Second")

    client.force_login(member)
    response = client.post(voting_submit_path(project, cycle), vote_data({first: 2, second: 1}))
    cycle.refresh_from_db()
    assert response.status_code == 302
    assert cycle.voting_status == FeedbackCycle.VotingStatus.OPEN

    client.force_login(facilitator)
    response = client.post(voting_submit_path(project, cycle), vote_data({first: 0, second: 3}))
    cycle.refresh_from_db()
    assert response.status_code == 302
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED


def test_facilitator_can_close_voting_early_and_closed_voting_rejects_updates(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Early Close Voting Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.OPEN)
    first = create_cluster(cycle, "First")
    second = create_cluster(cycle, "Second")

    client.force_login(member)
    member_close_response = client.post(voting_close_path(project, cycle))
    assert member_close_response.status_code == 404

    client.force_login(facilitator)
    close_response = client.post(voting_close_path(project, cycle))
    assert close_response.status_code == 302
    cycle.refresh_from_db()
    assert cycle.voting_status == FeedbackCycle.VotingStatus.CLOSED

    client.force_login(member)
    update_response = client.post(voting_submit_path(project, cycle), vote_data({first: 2, second: 1}))
    board_response = client.get(board_path(project, cycle))
    assert update_response.status_code == 404
    assert FeedbackClusterVote.objects.count() == 0
    assert "Save votes" not in board_response.content.decode()


def test_closed_results_are_ranked_deterministically_with_ties_and_zero_vote_clusters(client):
    first_voter = create_user(username="first-voter")
    second_voter = create_user(username="second-voter")
    author = create_user(username="card-author")
    facilitator = create_user(username="facilitator")
    project = create_project("Ranked Voting Project")
    add_membership(first_voter, project)
    add_membership(second_voter, project)
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.CLOSED)
    tied_earlier = create_cluster(cycle, "Tied earlier")
    winner = create_cluster(cycle, "Winner")
    zero = create_cluster(cycle, "Zero")
    tied_later = create_cluster(cycle, "Tied later")
    create_card(cycle, author, text="Anonymous-safe card", cluster=winner)
    FeedbackClusterVote.objects.create(cycle=cycle, voter=first_voter, cluster=winner, vote_count=3)
    FeedbackClusterVote.objects.create(cycle=cycle, voter=second_voter, cluster=winner, vote_count=1)
    FeedbackClusterVote.objects.create(cycle=cycle, voter=second_voter, cluster=tied_earlier, vote_count=1)
    FeedbackClusterVote.objects.create(cycle=cycle, voter=first_voter, cluster=tied_later, vote_count=0)
    FeedbackClusterVote.objects.create(cycle=cycle, voter=second_voter, cluster=tied_later, vote_count=1)

    ranked = ranked_clusters_for(cycle)
    assert [cluster["name"] for cluster in ranked] == [
        "Winner",
        "Tied earlier",
        "Tied later",
        "Zero",
    ]
    assert [cluster["vote_total"] for cluster in ranked] == [4, 1, 1, 0]

    client.force_login(facilitator)
    response = client.get(board_path(project, cycle))
    content = response.content.decode()
    assert content.index("Winner") < content.index("Tied earlier")
    assert content.index("Tied earlier") < content.index("Tied later")
    assert content.index("Tied later") < content.index("Zero")
    assert "4 votes" in content
    assert "1 vote" in content
    assert "0 votes" in content
    assert response.context["voting_progress"] == []
    assert "Voting progress" not in content


def test_clustering_controls_and_mutation_routes_are_rejected_after_voting_opens(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Frozen Cluster Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, voting_status=FeedbackCycle.VotingStatus.OPEN)
    source = create_cluster(cycle, "Source")
    target = create_cluster(cycle, "Target")
    card = create_card(cycle, member, text="Frozen card", cluster=source)
    client.force_login(facilitator)

    board_response = client.get(board_path(project, cycle))
    board_content = board_response.content.decode()
    for hidden_text in [
        "Create cluster",
        "AI cluster suggestions",
        "Rename cluster",
        "Move card",
        "Merge cluster",
        "Split cluster",
        "Accept suggestions",
    ]:
        assert hidden_text not in board_content

    responses = [
        client.post(cluster_create_path(project, cycle), {"name": "Late"}),
        client.post(cluster_rename_path(project, cycle, source), {"name": "Late"}),
        client.post(card_move_path(project, cycle, card), {"cluster": str(target.pk)}),
        client.post(cluster_merge_path(project, cycle, source), {"target_cluster": str(target.pk)}),
        client.post(cluster_split_path(project, cycle, source), {"name": "Late", "cards": [str(card.pk)]}),
        client.post(
            suggestions_accept_path(project, cycle),
            {
                "suggestion_count": "1",
                "suggestion-0-name": "Late suggestion",
                f"card-{card.pk}-suggestion": "0",
            },
        ),
    ]
    for response in responses:
        assert response.status_code == 404

    assert FeedbackCluster.objects.filter(cycle=cycle).count() == 2
    source.refresh_from_db()
    card.refresh_from_db()
    assert source.name == "Source"
    assert card.cluster == source


def test_anonymous_users_redirect_from_voting_routes_with_next(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)

    for path in [
        voting_open_path(project, cycle),
        voting_submit_path(project, cycle),
        voting_close_path(project, cycle),
    ]:
        response = client.post(path)
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={path}"


def test_voting_routes_protected_behavior_leaks_no_project_cycle_cluster_vote_or_card_data(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    outsider = create_user(username="outsider")
    admin = create_user(username="admin", is_staff=True, is_superuser=True)
    inactive = create_user(username="inactive", is_active=False)
    other_facilitator = create_user(username="other-facilitator")
    project = create_project("Secret Protected Project")
    other_project = create_project("Other Protected Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive, project)
    add_membership(other_facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(
        project,
        facilitator,
        label="Secret Protected Week",
        voting_status=FeedbackCycle.VotingStatus.OPEN,
    )
    other_cycle = create_cycle(
        other_project,
        other_facilitator,
        label="Other Protected Week",
        voting_status=FeedbackCycle.VotingStatus.OPEN,
    )
    cluster = create_cluster(cycle, "Secret protected cluster")
    create_cluster(other_cycle, "Other protected cluster")
    create_card(cycle, member, text="Secret protected card", cluster=cluster)
    FeedbackClusterVote.objects.create(cycle=cycle, voter=member, cluster=cluster, vote_count=3)
    secrets = [
        "Secret Protected Project",
        "Secret Protected Week",
        "Secret protected cluster",
        "Secret protected card",
        "3 votes",
        "Other Protected Project",
        "Other Protected Week",
        "Other protected cluster",
    ]
    protected_requests = []

    for user in [outsider, admin]:
        client.force_login(user)
        protected_requests.extend(
            [
                client.post(voting_open_path(project, cycle)),
                client.post(voting_submit_path(project, cycle), vote_data({cluster: 3})),
                client.post(voting_close_path(project, cycle)),
            ]
        )

    client.force_login(member)
    protected_requests.extend(
        [
            client.post(voting_open_path(project, cycle)),
            client.post(voting_close_path(project, cycle)),
            client.post(voting_submit_path(other_project, cycle), vote_data({cluster: 3})),
            client.post(voting_submit_path(project, other_cycle), vote_data({cluster: 3})),
        ]
    )

    client.force_login(inactive)
    inactive_path = voting_submit_path(project, cycle)
    inactive_response = client.post(inactive_path, vote_data({cluster: 3}))
    assert inactive_response.status_code == 302
    assert inactive_response["Location"] == f"{reverse('login')}?next={inactive_path}"

    for response in protected_requests:
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)
