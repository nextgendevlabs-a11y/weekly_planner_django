from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from projects.cluster_suggestions import (
    ClusterSuggestion,
    LocalDeterministicClusteringService,
)
from projects.models import (
    FeedbackCard,
    FeedbackCluster,
    FeedbackCycle,
    Membership,
    Project,
)


pytestmark = pytest.mark.django_db


class SuggestionParser(HTMLParser):
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


class FakeClusteringService:
    suggestions = []

    def suggest_clusters(self, cycle):
        return self.suggestions


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
):
    return FeedbackCycle.objects.create(
        project=project,
        facilitator=facilitator,
        label=label,
        status=status,
        opens_at=timezone.now(),
    )


def create_card(
    cycle,
    author,
    *,
    category=FeedbackCard.Category.START,
    text="Retrospective feedback",
    is_anonymous=False,
    cluster=None,
):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=category,
        text=text,
        is_anonymous=is_anonymous,
        cluster=cluster,
    )


def create_cluster(cycle, name="Release readiness"):
    return FeedbackCluster.objects.create(cycle=cycle, name=name)


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def suggestions_generate_path(project, cycle):
    return reverse(
        "feedback_cluster_suggestions_generate",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def suggestions_edit_path(project, cycle):
    return reverse(
        "feedback_cluster_suggestions_edit",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def suggestions_accept_path(project, cycle):
    return reverse(
        "feedback_cluster_suggestions_accept",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def suggestions_ignore_path(project, cycle):
    return reverse(
        "feedback_cluster_suggestions_ignore",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def suggestion_routes(project, cycle):
    return [
        suggestions_generate_path(project, cycle),
        suggestions_edit_path(project, cycle),
        suggestions_accept_path(project, cycle),
        suggestions_ignore_path(project, cycle),
    ]


def parser_from(response):
    parser = SuggestionParser()
    parser.feed(response.content.decode())
    return parser


def draft_post(names, assignments):
    data = {"suggestion_count": str(len(names))}
    for index, name in enumerate(names):
        data[f"suggestion-{index}-name"] = name
    for card, suggestion_index in assignments.items():
        data[f"card-{card.pk}-suggestion"] = (
            "" if suggestion_index is None else str(suggestion_index)
        )
    return data


def assert_board_state(cards, expected_clusters):
    for card, expected_cluster in expected_clusters.items():
        card.refresh_from_db()
        assert card.cluster == expected_cluster


def test_local_clustering_service_returns_stable_structured_suggestions():
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    start_card = create_card(cycle, member, text="Start pairing on release plans")
    stop_card = create_card(
        cycle,
        member,
        category=FeedbackCard.Category.STOP,
        text="Stop changing launch scope late",
    )

    service = LocalDeterministicClusteringService()
    first_result = service.suggest_clusters(cycle)
    second_result = service.suggest_clusters(cycle)

    assert first_result == second_result
    assert first_result == [
        ClusterSuggestion("Start themes", (start_card.pk,)),
        ClusterSuggestion("Stop themes", (stop_card.pk,)),
    ]


def test_facilitators_see_suggestion_controls_and_members_do_not(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)

    client.force_login(facilitator)
    facilitator_response = client.get(board_path(project, cycle))
    facilitator_content = facilitator_response.content.decode()
    facilitator_forms = parser_from(facilitator_response).forms

    assert "AI cluster suggestions" in facilitator_content
    assert "Generate suggestions" in facilitator_content
    assert {
        "action": suggestions_generate_path(project, cycle),
        "method": "post",
    } in facilitator_forms

    client.force_login(member)
    member_response = client.get(board_path(project, cycle))
    member_content = member_response.content.decode()
    member_form_actions = [form["action"] for form in parser_from(member_response).forms]

    assert "AI cluster suggestions" not in member_content
    assert "Generate suggestions" not in member_content
    assert suggestions_generate_path(project, cycle) not in member_form_actions
    assert suggestions_edit_path(project, cycle) not in member_form_actions
    assert suggestions_accept_path(project, cycle) not in member_form_actions
    assert suggestions_ignore_path(project, cycle) not in member_form_actions


def test_anonymous_users_are_redirected_from_suggestion_routes_with_next(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)

    for path in suggestion_routes(project, cycle):
        response = client.post(path)

        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={path}"


def test_non_facilitators_cannot_post_to_suggestion_routes_without_leakage(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    outsider = create_user(username="outsider")
    admin = create_user(username="admin", is_staff=True, is_superuser=True)
    project = create_project("Secret Suggestion Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Secret Suggestion Week")
    existing_cluster = create_cluster(cycle, "Secret manual theme")
    card = create_card(cycle, member, text="Secret suggestion card", cluster=existing_cluster)
    post_data = draft_post(["Forbidden theme"], {card: 0})

    for user in [member, outsider, admin]:
        client.force_login(user)
        for path in suggestion_routes(project, cycle):
            response = client.post(path, post_data)
            content = response.content.decode()

            assert response.status_code == 404
            assert "Secret Suggestion Project" not in content
            assert "Secret Suggestion Week" not in content
            assert "Secret manual theme" not in content
            assert "Secret suggestion card" not in content

    assert FeedbackCluster.objects.count() == 1
    card.refresh_from_db()
    assert card.cluster == existing_cluster


def test_inactive_user_cannot_post_to_suggestion_routes(client):
    inactive = create_user(username="inactive", is_active=False)
    project = create_project()
    add_membership(inactive, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, inactive)
    client.force_login(inactive)

    for path in suggestion_routes(project, cycle):
        response = client.post(path)

        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={path}"


@pytest.mark.parametrize(
    "status",
    [FeedbackCycle.Status.COLLECTING_FEEDBACK, FeedbackCycle.Status.COMPLETED],
)
def test_suggestion_routes_are_blocked_outside_retrospective_state(client, status):
    facilitator = create_user(username=f"facilitator-{status}")
    project = create_project(f"Blocked Suggestion {status}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, status=status)
    card = create_card(cycle, facilitator, text="Blocked card")
    client.force_login(facilitator)

    for path in suggestion_routes(project, cycle):
        response = client.post(path, draft_post(["Blocked theme"], {card: 0}))

        assert response.status_code == 404

    assert FeedbackCluster.objects.count() == 0
    card.refresh_from_db()
    assert card.cluster is None


def test_generating_suggestions_shows_draft_preview_without_mutating_board(
    client,
    monkeypatch,
):
    facilitator = create_user(
        username="facilitator",
        first_name="Frances",
        last_name="Lead",
        email="facilitator@example.test",
    )
    member = create_user(username="member")
    anonymous_author = create_user(
        username="hidden-author",
        first_name="Hidden",
        last_name="Author",
        email="hidden@example.test",
    )
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    manual_cluster = create_cluster(cycle, "Existing manual theme")
    manual_card = create_card(
        cycle,
        member,
        category=FeedbackCard.Category.CONTINUE,
        text="Continue manual grouping",
        cluster=manual_cluster,
    )
    suggested_card = create_card(
        cycle,
        anonymous_author,
        category=FeedbackCard.Category.STOP,
        text="Stop surprise priority changes",
        is_anonymous=True,
    )
    FakeClusteringService.suggestions = [
        ClusterSuggestion("Priority clarity", (manual_card.pk, suggested_card.pk))
    ]
    monkeypatch.setattr(
        "weekly_planner.views.get_clustering_service",
        lambda: FakeClusteringService(),
    )
    client.force_login(facilitator)

    response = client.post(suggestions_generate_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Draft cluster suggestions" in content
    assert "Priority clarity" in content
    assert "Continue manual grouping" in content
    assert "Continue - member" in content
    assert "Stop surprise priority changes" in content
    assert "Stop - Anonymous contributor" in content
    assert "hidden-author" not in content
    assert "Hidden Author" not in content
    assert "hidden@example.test" not in content
    assert FeedbackCluster.objects.count() == 1
    assert_board_state({manual_card, suggested_card}, {manual_card: manual_cluster, suggested_card: None})


def test_editing_draft_names_and_membership_updates_preview_without_mutating_board(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    manual_cluster = create_cluster(cycle, "Existing manual theme")
    first_card = create_card(cycle, member, text="Start planning earlier", cluster=manual_cluster)
    second_card = create_card(cycle, member, text="Continue release notes")
    client.force_login(facilitator)

    response = client.post(
        suggestions_edit_path(project, cycle),
        draft_post(
            ["Planning clarity", "Release habits"],
            {first_card: 1, second_card: None},
        ),
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Planning clarity" in content
    assert "Release habits" in content
    assert 'name="card-{}-suggestion"'.format(first_card.pk) in content
    assert FeedbackCluster.objects.count() == 1
    assert_board_state({first_card, second_card}, {first_card: manual_cluster, second_card: None})


def test_ignoring_draft_suggestions_removes_draft_without_mutating_board(
    client,
    monkeypatch,
):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, member, text="Start clearer handoffs")
    FakeClusteringService.suggestions = [ClusterSuggestion("Handoffs", (card.pk,))]
    monkeypatch.setattr(
        "weekly_planner.views.get_clustering_service",
        lambda: FakeClusteringService(),
    )
    client.force_login(facilitator)

    client.post(suggestions_generate_path(project, cycle))
    ignore_response = client.post(suggestions_ignore_path(project, cycle))
    board_response = client.get(board_path(project, cycle))

    assert ignore_response.status_code == 302
    assert "Draft cluster suggestions" not in board_response.content.decode()
    assert FeedbackCluster.objects.count() == 0
    card.refresh_from_db()
    assert card.cluster is None


def test_accepting_valid_draft_creates_clusters_and_moves_only_included_cards(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    manual_cluster = create_cluster(cycle, "Existing manual theme")
    clustered_included = create_card(
        cycle,
        member,
        text="Start risk reviews",
        cluster=manual_cluster,
    )
    clustered_omitted = create_card(
        cycle,
        member,
        text="Continue demo prep",
        cluster=manual_cluster,
    )
    ungrouped_included = create_card(cycle, member, text="Stop late scope changes")
    ungrouped_omitted = create_card(cycle, member, text="Continue team notes")
    client.force_login(facilitator)

    response = client.post(
        suggestions_accept_path(project, cycle),
        draft_post(
            ["Risk planning", "Scope control"],
            {
                clustered_included: 0,
                clustered_omitted: None,
                ungrouped_included: 1,
                ungrouped_omitted: None,
            },
        ),
    )

    assert response.status_code == 302
    assert FeedbackCluster.objects.filter(pk=manual_cluster.pk, name="Existing manual theme").exists()
    risk_cluster = FeedbackCluster.objects.get(name="Risk planning")
    scope_cluster = FeedbackCluster.objects.get(name="Scope control")
    assert_board_state(
        {clustered_included, clustered_omitted, ungrouped_included, ungrouped_omitted},
        {
            clustered_included: risk_cluster,
            clustered_omitted: manual_cluster,
            ungrouped_included: scope_cluster,
            ungrouped_omitted: None,
        },
    )

    board_response = client.get(board_path(project, cycle))
    content = board_response.content.decode()
    assert "Risk planning" in content
    assert "Scope control" in content
    assert "Draft cluster suggestions" not in content


def test_accepting_blank_name_shows_error_without_mutating_board(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    manual_cluster = create_cluster(cycle, "Existing manual theme")
    card = create_card(cycle, member, text="Start naming risks", cluster=manual_cluster)
    client.force_login(facilitator)

    response = client.post(
        suggestions_accept_path(project, cycle),
        draft_post(["   "], {card: 0}),
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Cluster name cannot be empty." in content
    assert FeedbackCluster.objects.count() == 1
    card.refresh_from_db()
    assert card.cluster == manual_cluster


def test_accepting_no_suggested_clusters_shows_error_without_mutating_board(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, member, text="Start with no suggestions")
    client.force_login(facilitator)

    response = client.post(
        suggestions_accept_path(project, cycle),
        draft_post([], {card: None}),
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "There are no draft suggestions to accept." in content
    assert FeedbackCluster.objects.count() == 0
    card.refresh_from_db()
    assert card.cluster is None


def test_no_service_suggestions_show_empty_state_without_mutating_board(
    client,
    monkeypatch,
):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, member, text="Start with sparse feedback")
    FakeClusteringService.suggestions = []
    monkeypatch.setattr(
        "weekly_planner.views.get_clustering_service",
        lambda: FakeClusteringService(),
    )
    client.force_login(facilitator)

    response = client.post(suggestions_generate_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "No clustering suggestions were found." in content
    assert FeedbackCluster.objects.count() == 0
    card.refresh_from_db()
    assert card.cluster is None


def test_no_revealed_cards_show_empty_state_without_creating_clusters(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    client.force_login(facilitator)

    response = client.post(suggestions_generate_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "No revealed feedback cards are available for suggestions." in content
    assert FeedbackCluster.objects.count() == 0


def test_tampered_cross_cycle_acceptance_is_rejected_atomically_without_leakage(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Secret Accept Project")
    other_project = create_project("Other Accept Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Secret Accept Week")
    other_cycle = create_cycle(other_project, facilitator, label="Other Accept Week")
    card = create_card(cycle, member, text="Secret accepted card")
    other_card = create_card(other_cycle, member, text="Other cycle card")
    client.force_login(facilitator)
    post_data = draft_post(["Valid looking theme"], {card: 0})
    post_data[f"card-{other_card.pk}-suggestion"] = "0"

    response = client.post(suggestions_accept_path(project, cycle), post_data)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Draft contains a card outside this cycle." in content
    assert "Other cycle card" not in content
    assert FeedbackCluster.objects.count() == 0
    card.refresh_from_db()
    other_card.refresh_from_db()
    assert card.cluster is None
    assert other_card.cluster is None


def test_invalid_draft_membership_index_is_rejected_atomically(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, member, text="Start tamper checks")
    client.force_login(facilitator)

    response = client.post(
        suggestions_accept_path(project, cycle),
        draft_post(["Valid name"], {card: 3}),
    )

    assert response.status_code == 200
    assert "Draft suggestions could not be read." in response.content.decode()
    assert FeedbackCluster.objects.count() == 0
    card.refresh_from_db()
    assert card.cluster is None
