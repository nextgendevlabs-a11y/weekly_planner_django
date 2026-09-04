from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from projects.forms import FeedbackClusterDiscussionForm
from projects.models import (
    FeedbackCard,
    FeedbackCluster,
    FeedbackClusterVote,
    FeedbackCycle,
    Membership,
    Project,
)


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
    voting_status=FeedbackCycle.VotingStatus.CLOSED,
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        voting_status=voting_status,
        opens_at=timezone.now(),
    )


def create_cluster(cycle, name="Release readiness", **kwargs):
    return FeedbackCluster.objects.create(cycle=cycle, name=name, **kwargs)


def create_card(cycle, author, *, text="Retrospective feedback", cluster=None):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=FeedbackCard.Category.START,
        text=text,
        cluster=cluster,
    )


def create_vote(cycle, voter, cluster, vote_count):
    return FeedbackClusterVote.objects.create(
        cycle=cycle,
        voter=voter,
        cluster=cluster,
        vote_count=vote_count,
    )


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def discussion_update_path(project, cycle, cluster):
    return reverse(
        "feedback_cluster_discussion_update",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "cluster_id": cluster.pk,
        },
    )


def parser_from(response):
    parser = BoardParser()
    parser.feed(response.content.decode())
    return parser


def assert_no_secret_leak(response, secrets):
    content = response.content.decode()
    for secret in secrets:
        assert secret not in content


def test_discussion_state_has_initial_and_limited_valid_statuses():
    facilitator = create_user(username="facilitator")
    project = create_project("Discussion Model Project")
    cycle = create_cycle(project, facilitator)

    cluster = create_cluster(cycle, "Planning")

    assert cluster.discussion_status == FeedbackCluster.DiscussionStatus.PENDING
    assert cluster.get_discussion_status_display() == "Not started"
    assert cluster.discussion_notes == ""

    for status in [
        FeedbackCluster.DiscussionStatus.PENDING,
        FeedbackCluster.DiscussionStatus.DISCUSSED,
        FeedbackCluster.DiscussionStatus.SKIPPED,
        FeedbackCluster.DiscussionStatus.DEFERRED,
    ]:
        cluster.discussion_status = status
        cluster.full_clean()

    cluster.discussion_status = "done-ish"
    with pytest.raises(ValidationError) as error:
        cluster.full_clean()
    assert "discussion_status" in error.value.message_dict

    with pytest.raises(IntegrityError), transaction.atomic():
        create_cluster(cycle, "Invalid stored status", discussion_status="done-ish")


def test_discussion_ui_is_available_only_for_closed_voting_retrospective_cycles(client):
    facilitator = create_user(username="facilitator")
    project = create_project("Closed Discussion Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    closed_cycle = create_cycle(
        project,
        facilitator,
        label="Closed Week",
        voting_status=FeedbackCycle.VotingStatus.CLOSED,
    )
    closed_cluster = create_cluster(
        closed_cycle,
        "Closed topic",
        discussion_status=FeedbackCluster.DiscussionStatus.DEFERRED,
        discussion_notes="Saved closed-cycle notes",
    )
    client.force_login(facilitator)

    closed_response = client.get(board_path(project, closed_cycle))
    closed_content = closed_response.content.decode()
    closed_forms = parser_from(closed_response).forms

    assert closed_response.status_code == 200
    assert "Ranked discussion agenda" in closed_content
    assert "Topic status:" in closed_content
    assert "Deferred" in closed_content
    assert "Discussion notes" in closed_content
    assert "Saved closed-cycle notes" in closed_content
    assert "Save discussion topic" in closed_content
    assert {
        "action": discussion_update_path(project, closed_cycle, closed_cluster),
        "method": "post",
    } in closed_forms

    gated_cases = [
        (
            create_project("Collecting Discussion Project"),
            FeedbackCycle.Status.COLLECTING_FEEDBACK,
            FeedbackCycle.VotingStatus.CLOSED,
            "Collecting-only hidden notes",
        ),
        (
            create_project("Completed Discussion Project"),
            FeedbackCycle.Status.COMPLETED,
            FeedbackCycle.VotingStatus.CLOSED,
            "Completed-only hidden notes",
        ),
        (
            create_project("Clustering Discussion Project"),
            FeedbackCycle.Status.RETROSPECTIVE,
            FeedbackCycle.VotingStatus.CLUSTERING,
            "Clustering-stage hidden notes",
        ),
        (
            create_project("Open Discussion Project"),
            FeedbackCycle.Status.RETROSPECTIVE,
            FeedbackCycle.VotingStatus.OPEN,
            "Open-voting hidden notes",
        ),
    ]

    for gated_project, status, voting_status, hidden_notes in gated_cases:
        add_membership(facilitator, gated_project, Membership.Role.FACILITATOR)
        cycle = create_cycle(
            gated_project,
            facilitator,
            status=status,
            voting_status=voting_status,
        )
        cluster = create_cluster(cycle, "Hidden discussion topic", discussion_notes=hidden_notes)

        board_response = client.get(board_path(gated_project, cycle))
        update_response = client.post(
            discussion_update_path(gated_project, cycle, cluster),
            {
                "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
                "discussion_notes": "Should not save",
            },
        )

        if status == FeedbackCycle.Status.RETROSPECTIVE:
            content = board_response.content.decode()
            assert board_response.status_code == 200
            assert "Ranked discussion agenda" not in content
            assert "Topic status:" not in content
            assert "Discussion notes" not in content
            assert "Save discussion topic" not in content
            assert hidden_notes not in content
            assert update_response.status_code == 404
        else:
            assert board_response.status_code == 404
            assert update_response.status_code == 404

        cluster.refresh_from_db()
        assert cluster.discussion_status == FeedbackCluster.DiscussionStatus.PENDING
        assert cluster.discussion_notes == hidden_notes


def test_discussion_agenda_lists_requested_cycle_clusters_in_vote_result_order(client):
    first_voter = create_user(username="first-voter")
    second_voter = create_user(username="second-voter")
    facilitator = create_user(username="facilitator")
    author = create_user(username="author")
    project = create_project("Ranked Discussion Project")
    other_project = create_project("Other Discussion Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(first_voter, project)
    add_membership(second_voter, project)
    cycle = create_cycle(project, facilitator)
    tied_earlier = create_cluster(cycle, "Tied earlier")
    winner = create_cluster(cycle, "Winner")
    zero = create_cluster(cycle, "Zero")
    tied_later = create_cluster(cycle, "Tied later")
    create_card(cycle, author, text="Card stays visible", cluster=winner)
    other_cycle = create_cycle(
        other_project,
        facilitator,
        label="Other Week",
        status=FeedbackCycle.Status.COMPLETED,
    )
    create_cluster(
        other_cycle,
        "Other cycle hidden topic",
        discussion_notes="Other hidden notes",
    )
    create_vote(cycle, first_voter, winner, 3)
    create_vote(cycle, second_voter, winner, 1)
    create_vote(cycle, second_voter, tied_earlier, 1)
    create_vote(cycle, first_voter, tied_later, 0)
    create_vote(cycle, second_voter, tied_later, 1)
    client.force_login(facilitator)

    response = client.get(board_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert [topic["name"] for topic in response.context["discussion_topics"]] == [
        "Winner",
        "Tied earlier",
        "Tied later",
        "Zero",
    ]
    assert [topic["vote_total"] for topic in response.context["discussion_topics"]] == [
        4,
        1,
        1,
        0,
    ]
    assert content.index("Winner") < content.index("Tied earlier")
    assert content.index("Tied earlier") < content.index("Tied later")
    assert content.index("Tied later") < content.index("Zero")
    assert "4 votes" in content
    assert "1 vote" in content
    assert "0 votes" in content
    assert "Other cycle hidden topic" not in content
    assert "Other hidden notes" not in content


def test_facilitator_can_save_edit_change_and_clear_topic_discussion_without_other_mutations(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Editable Discussion Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    first = create_cluster(cycle, "First topic")
    second = create_cluster(cycle, "Second topic")
    card = create_card(cycle, member, text="Keep this card unchanged", cluster=first)
    vote = create_vote(cycle, member, first, 3)
    second_original = {
        "name": second.name,
        "discussion_status": second.discussion_status,
        "discussion_notes": second.discussion_notes,
    }
    card_original = {
        "text": card.text,
        "category": card.category,
        "author": card.author,
        "cluster": card.cluster,
        "is_anonymous": card.is_anonymous,
    }
    client.force_login(facilitator)

    save_response = client.post(
        discussion_update_path(project, cycle, first),
        {
            "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
            "discussion_notes": "Talked through release risk.",
        },
    )
    first.refresh_from_db()
    assert save_response.status_code == 302
    assert first.discussion_status == FeedbackCluster.DiscussionStatus.DISCUSSED
    assert first.discussion_notes == "Talked through release risk."

    board_response = client.get(board_path(project, cycle))
    assert "Discussed" in board_response.content.decode()
    assert "Talked through release risk." in board_response.content.decode()

    notes_only_response = client.post(
        discussion_update_path(project, cycle, first),
        {"discussion_notes": "Edited note only."},
    )
    first.refresh_from_db()
    assert notes_only_response.status_code == 302
    assert first.discussion_status == FeedbackCluster.DiscussionStatus.DISCUSSED
    assert first.discussion_notes == "Edited note only."

    status_change_response = client.post(
        discussion_update_path(project, cycle, first),
        {
            "discussion_status": FeedbackCluster.DiscussionStatus.SKIPPED,
            "discussion_notes": "Edited note only.",
        },
    )
    first.refresh_from_db()
    assert status_change_response.status_code == 302
    assert first.discussion_status == FeedbackCluster.DiscussionStatus.SKIPPED
    assert first.discussion_notes == "Edited note only."

    defer_response = client.post(
        discussion_update_path(project, cycle, first),
        {
            "discussion_status": FeedbackCluster.DiscussionStatus.DEFERRED,
            "discussion_notes": "Deferred until next retro.",
        },
    )
    first.refresh_from_db()
    assert defer_response.status_code == 302
    assert first.discussion_status == FeedbackCluster.DiscussionStatus.DEFERRED
    assert first.discussion_notes == "Deferred until next retro."

    clear_response = client.post(
        discussion_update_path(project, cycle, first),
        {
            "discussion_status": FeedbackCluster.DiscussionStatus.DEFERRED,
            "discussion_notes": "   ",
        },
    )
    first.refresh_from_db()
    assert clear_response.status_code == 302
    assert first.discussion_status == FeedbackCluster.DiscussionStatus.DEFERRED
    assert first.discussion_notes == ""

    second.refresh_from_db()
    card.refresh_from_db()
    vote.refresh_from_db()
    assert second.name == second_original["name"]
    assert second.discussion_status == second_original["discussion_status"]
    assert second.discussion_notes == second_original["discussion_notes"]
    assert card.text == card_original["text"]
    assert card.category == card_original["category"]
    assert card.author == card_original["author"]
    assert card.cluster == card_original["cluster"]
    assert card.is_anonymous == card_original["is_anonymous"]
    assert vote.vote_count == 3
    assert first.name == "First topic"
    assert FeedbackCluster.objects.count() == 2
    assert FeedbackCard.objects.count() == 1
    assert FeedbackClusterVote.objects.count() == 1


def test_saved_discussion_notes_persist_for_facilitator_and_read_only_member_views(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Readable Discussion Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(
        cycle,
        "Shared topic",
        discussion_status=FeedbackCluster.DiscussionStatus.DISCUSSED,
        discussion_notes="Persistent shared note.",
    )

    client.force_login(facilitator)
    facilitator_response = client.get(board_path(project, cycle))
    facilitator_content = facilitator_response.content.decode()
    assert "Persistent shared note." in facilitator_content
    assert "Discussed" in facilitator_content
    assert "Save discussion topic" in facilitator_content

    client.force_login(member)
    member_response = client.get(board_path(project, cycle))
    member_content = member_response.content.decode()
    member_forms = parser_from(member_response).forms
    assert member_response.status_code == 200
    assert "Ranked discussion agenda" in member_content
    assert "Persistent shared note." in member_content
    assert "Discussed" in member_content
    assert "Save discussion topic" not in member_content
    assert discussion_update_path(project, cycle, cluster) not in [
        form["action"] for form in member_forms
    ]


def test_invalid_discussion_status_is_rejected_without_changing_status_or_notes(client):
    facilitator = create_user(username="facilitator")
    project = create_project("Invalid Discussion Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(
        cycle,
        "Invalid topic",
        discussion_status=FeedbackCluster.DiscussionStatus.SKIPPED,
        discussion_notes="Existing note.",
    )
    form = FeedbackClusterDiscussionForm(
        {"discussion_status": "invalid", "discussion_notes": "New note"},
        cluster=cluster,
    )
    assert form.is_valid() is False
    client.force_login(facilitator)

    response = client.post(
        discussion_update_path(project, cycle, cluster),
        {"discussion_status": "invalid", "discussion_notes": "New note"},
    )

    assert response.status_code == 200
    assert "Choose a valid discussion status." in response.content.decode()
    cluster.refresh_from_db()
    assert cluster.discussion_status == FeedbackCluster.DiscussionStatus.SKIPPED
    assert cluster.discussion_notes == "Existing note."


def test_anonymous_users_redirect_from_board_and_discussion_mutation_with_next(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(cycle)

    for path in [board_path(project, cycle), discussion_update_path(project, cycle, cluster)]:
        response = client.post(path) if "discussion" in path else client.get(path)
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={path}"


def test_protected_users_and_non_facilitators_cannot_view_or_mutate_without_leakage(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    outsider = create_user(username="outsider")
    admin = create_user(username="admin", is_staff=True, is_superuser=True)
    inactive = create_user(username="inactive", is_active=False)
    project = create_project("Secret Discussion Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(inactive, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Secret Discussion Week")
    cluster = create_cluster(
        cycle,
        "Secret discussion topic",
        discussion_status=FeedbackCluster.DiscussionStatus.DEFERRED,
        discussion_notes="Secret discussion note.",
    )
    create_card(cycle, member, text="Secret discussion card", cluster=cluster)
    create_vote(cycle, member, cluster, 3)
    secrets = [
        "Secret Discussion Project",
        "Secret Discussion Week",
        "Secret discussion topic",
        "Secret discussion note.",
        "Secret discussion card",
        "3 votes",
        "Deferred",
    ]

    for user in [outsider, admin]:
        client.force_login(user)
        view_response = client.get(board_path(project, cycle))
        update_response = client.post(
            discussion_update_path(project, cycle, cluster),
            {
                "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
                "discussion_notes": "Leaked write",
            },
        )
        for response in [view_response, update_response]:
            assert response.status_code == 404
            assert_no_secret_leak(response, secrets)

    client.force_login(member)
    member_update_response = client.post(
        discussion_update_path(project, cycle, cluster),
        {
            "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
            "discussion_notes": "Member write",
        },
    )
    assert member_update_response.status_code == 404
    assert_no_secret_leak(member_update_response, secrets)

    inactive_path = discussion_update_path(project, cycle, cluster)
    client.force_login(inactive)
    inactive_view_response = client.get(board_path(project, cycle))
    inactive_update_response = client.post(
        inactive_path,
        {
            "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
            "discussion_notes": "Inactive write",
        },
    )
    assert inactive_view_response.status_code == 302
    assert inactive_view_response["Location"] == (
        f"{reverse('login')}?next={board_path(project, cycle)}"
    )
    assert inactive_update_response.status_code == 302
    assert inactive_update_response["Location"] == f"{reverse('login')}?next={inactive_path}"

    cluster.refresh_from_db()
    assert cluster.discussion_status == FeedbackCluster.DiscussionStatus.DEFERRED
    assert cluster.discussion_notes == "Secret discussion note."


def test_cross_cycle_and_project_discussion_tampering_is_rejected_without_leakage(client):
    facilitator = create_user(username="facilitator")
    project = create_project("Secret Tamper Project")
    other_project = create_project("Other Tamper Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Secret Tamper Week")
    other_cycle = create_cycle(other_project, facilitator, label="Other Tamper Week")
    cluster = create_cluster(
        cycle,
        "Secret tamper topic",
        discussion_notes="Secret tamper note",
    )
    other_cluster = create_cluster(
        other_cycle,
        "Other tamper topic",
        discussion_notes="Other tamper note",
    )
    secrets = [
        "Secret Tamper Project",
        "Secret Tamper Week",
        "Secret tamper topic",
        "Secret tamper note",
        "Other Tamper Project",
        "Other Tamper Week",
        "Other tamper topic",
        "Other tamper note",
    ]
    client.force_login(facilitator)

    responses = [
        client.post(
            discussion_update_path(project, cycle, other_cluster),
            {
                "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
                "discussion_notes": "Wrong topic",
            },
        ),
        client.post(
            discussion_update_path(project, other_cycle, other_cluster),
            {
                "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
                "discussion_notes": "Wrong cycle",
            },
        ),
        client.post(
            discussion_update_path(other_project, cycle, cluster),
            {
                "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
                "discussion_notes": "Wrong project",
            },
        ),
    ]

    for response in responses:
        assert response.status_code == 404
        assert_no_secret_leak(response, secrets)

    cluster.refresh_from_db()
    other_cluster.refresh_from_db()
    assert cluster.discussion_status == FeedbackCluster.DiscussionStatus.PENDING
    assert cluster.discussion_notes == "Secret tamper note"
    assert other_cluster.discussion_status == FeedbackCluster.DiscussionStatus.PENDING
    assert other_cluster.discussion_notes == "Other tamper note"


def test_discussion_scope_does_not_create_provider_or_summary_data(client):
    facilitator = create_user(username="facilitator")
    project = create_project("Scope Discussion Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(cycle, "Manual notes only")
    model_names_before = {model.__name__ for model in Project._meta.apps.get_models()}
    client.force_login(facilitator)

    response = client.post(
        discussion_update_path(project, cycle, cluster),
        {
            "discussion_status": FeedbackCluster.DiscussionStatus.DISCUSSED,
            "discussion_notes": "Plain manual topic note.",
        },
    )
    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode().lower()
    model_names_after = {model.__name__ for model in Project._meta.apps.get_models()}

    assert response.status_code == 302
    assert model_names_after == model_names_before
    assert "transcription provider" not in content
    assert "summary" not in content
    assert "ai-extracted" not in content
