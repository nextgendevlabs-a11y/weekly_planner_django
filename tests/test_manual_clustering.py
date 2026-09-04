from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    FeedbackCard,
    FeedbackCluster,
    FeedbackCycle,
    Membership,
    Project,
)


pytestmark = pytest.mark.django_db


class BoardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.hrefs = []
        self.nav_hrefs = []
        self.nav_form_actions = []
        self._in_nav = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "nav":
            self._in_nav = True
        if tag == "a" and "href" in attributes:
            self.hrefs.append(attributes["href"])
            if self._in_nav:
                self.nav_hrefs.append(attributes["href"])
        if tag == "form" and "action" in attributes:
            form = {
                "action": attributes["action"],
                "method": attributes.get("method", "get").lower(),
            }
            self.forms.append(form)
            if self._in_nav:
                self.nav_form_actions.append(attributes["action"])

    def handle_endtag(self, tag):
        if tag == "nav":
            self._in_nav = False


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


def parser_from(response):
    parser = BoardParser()
    parser.feed(response.content.decode())
    return parser


def test_feedback_cluster_has_expected_fields_and_relationships():
    facilitator = create_user(username="facilitator")
    project = create_project("Platform Retrospective")
    cycle = create_cycle(project, facilitator)

    cluster = create_cluster(cycle, "Planning focus")

    assert cluster.cycle == cycle
    assert cluster.name == "Planning focus"
    assert cluster.created_at is not None
    assert cluster.updated_at is not None
    assert str(cluster) == "Planning focus (Week 34 Retrospective (Platform Retrospective))"


def test_feedback_cluster_rejects_blank_or_whitespace_name():
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    cluster = FeedbackCluster(cycle=cycle, name="   ")

    with pytest.raises(ValidationError) as error:
        cluster.full_clean()

    assert "name" in error.value.message_dict
    assert "Cluster name cannot be empty." in error.value.message_dict["name"]


def test_feedback_card_cluster_must_belong_to_the_same_cycle():
    author = create_user(username="author")
    facilitator = create_user(username="facilitator")
    project = create_project()
    other_project = create_project("Other Project")
    cycle = create_cycle(project, facilitator)
    other_cycle = create_cycle(
        other_project,
        facilitator,
        label="Other Week",
    )
    other_cluster = create_cluster(other_cycle, "Other theme")
    card = FeedbackCard(
        cycle=cycle,
        author=author,
        category=FeedbackCard.Category.START,
        text="Start sharing risk earlier",
        cluster=other_cluster,
    )

    with pytest.raises(ValidationError) as error:
        card.full_clean()

    assert "cluster" in error.value.message_dict
    assert "same feedback cycle" in error.value.message_dict["cluster"][0]


def test_feedback_cards_can_be_clustered_once_or_left_ungrouped():
    author = create_user(username="author")
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(cycle)
    card = create_card(cycle, author)
    clustered_card = create_card(
        cycle,
        author,
        text="Continue writing crisp handoffs",
        cluster=cluster,
    )

    assert card.cluster is None
    assert clustered_card.cluster == cluster
    assert list(cluster.feedback_cards.all()) == [clustered_card]


def test_deleting_feedback_cycle_removes_clusters():
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    create_cluster(cycle)

    cycle.delete()

    assert FeedbackCluster.objects.count() == 0


def test_board_shows_ungrouped_and_clustered_cards_separately(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Manual Cluster Retrospective")
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Week 34")
    cluster = create_cluster(cycle, "Release readiness")
    clustered_card = create_card(
        cycle,
        member,
        category=FeedbackCard.Category.CONTINUE,
        text="Continue checking launch risks",
        cluster=cluster,
    )
    ungrouped_card = create_card(
        cycle,
        member,
        category=FeedbackCard.Category.STOP,
        text="Stop late scope changes",
    )
    client.force_login(member)

    response = client.get(board_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "projects/retrospective_board.html" in [
        template.name for template in response.templates
    ]
    assert "base.html" in [template.name for template in response.templates]
    assert "Manual Cluster Retrospective" in content
    assert "Week 34" in content
    assert "Themes" in content
    assert "Ungrouped feedback" in content
    assert "Release readiness" in content
    assert "Continue checking launch risks" in content
    assert "Continue - member" in content
    assert "Stop late scope changes" in content
    assert response.context["clusters"][0]["cards"][0]["id"] == clustered_card.pk
    stop_section = [
        section
        for section in response.context["category_sections"]
        if section["value"] == FeedbackCard.Category.STOP
    ][0]
    assert stop_section["cards"][0]["id"] == ungrouped_card.pk


def test_newly_revealed_cards_appear_ungrouped_until_moved(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Ungrouped Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, member, text="Start refining handoffs")
    cluster = create_cluster(cycle, "Handoffs")
    client.force_login(facilitator)

    response = client.get(board_path(project, cycle))
    section_cards = response.context["category_sections"][0]["cards"]

    assert section_cards[0]["id"] == card.pk

    client.post(card_move_path(project, cycle, card), {"cluster": str(cluster.pk)})
    response = client.get(board_path(project, cycle))

    assert response.context["category_sections"][0]["cards"] == []
    assert response.context["clusters"][0]["cards"][0]["id"] == card.pk


def test_facilitators_see_cluster_controls_and_members_do_not(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(cycle)
    create_card(cycle, member, cluster=cluster)

    client.force_login(facilitator)
    facilitator_response = client.get(board_path(project, cycle))
    facilitator_content = facilitator_response.content.decode()
    facilitator_forms = parser_from(facilitator_response).forms

    assert "Create cluster" in facilitator_content
    assert "Rename cluster" in facilitator_content
    assert "Move card" in facilitator_content
    assert "Merge cluster" in facilitator_content
    assert "Split cluster" in facilitator_content
    assert {"action": cluster_create_path(project, cycle), "method": "post"} in (
        facilitator_forms
    )
    assert {"action": cluster_rename_path(project, cycle, cluster), "method": "post"} in (
        facilitator_forms
    )

    client.force_login(member)
    member_response = client.get(board_path(project, cycle))
    member_content = member_response.content.decode()
    member_forms = parser_from(member_response).forms

    assert "Release readiness" in member_content
    assert "Create cluster" not in member_content
    assert "Rename cluster" not in member_content
    assert "Move card" not in member_content
    assert "Merge cluster" not in member_content
    assert "Split cluster" not in member_content
    assert cluster_create_path(project, cycle) not in [
        form["action"] for form in member_forms
    ]


def test_valid_cluster_create_and_rename_return_to_board(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    client.force_login(facilitator)

    create_response = client.post(
        cluster_create_path(project, cycle),
        {"name": "Release risks"},
    )

    assert create_response.status_code == 302
    assert create_response["Location"] == board_path(project, cycle)
    cluster = FeedbackCluster.objects.get()
    assert cluster.cycle == cycle
    assert cluster.name == "Release risks"

    rename_response = client.post(
        cluster_rename_path(project, cycle, cluster),
        {"name": "Planning risks"},
    )

    assert rename_response.status_code == 302
    cluster.refresh_from_db()
    assert cluster.name == "Planning risks"


def test_invalid_cluster_create_and_rename_show_errors_without_changes(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(cycle, "Original theme")
    client.force_login(facilitator)

    create_response = client.post(cluster_create_path(project, cycle), {"name": "   "})
    rename_response = client.post(
        cluster_rename_path(project, cycle, cluster),
        {"name": "   "},
    )

    assert create_response.status_code == 200
    assert rename_response.status_code == 200
    assert FeedbackCluster.objects.count() == 1
    cluster.refresh_from_db()
    assert cluster.name == "Original theme"
    assert "Cluster name cannot be empty." in create_response.content.decode()
    assert "Cluster name cannot be empty." in rename_response.content.decode()
    assert "errorlist" in create_response.content.decode()
    assert "errorlist" in rename_response.content.decode()


def test_moving_cards_into_between_and_back_from_clusters_preserves_card_fields(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    first_cluster = create_cluster(cycle, "Planning")
    second_cluster = create_cluster(cycle, "Quality")
    card = create_card(
        cycle,
        member,
        category=FeedbackCard.Category.STOP,
        text="Stop changing scope late",
        is_anonymous=True,
    )
    original_snapshot = {
        "text": card.text,
        "category": card.category,
        "author": card.author,
        "is_anonymous": card.is_anonymous,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }
    client.force_login(facilitator)

    first_response = client.post(
        card_move_path(project, cycle, card),
        {"cluster": str(first_cluster.pk)},
    )
    card.refresh_from_db()
    second_response = client.post(
        card_move_path(project, cycle, card),
        {"cluster": str(second_cluster.pk)},
    )
    card.refresh_from_db()
    ungrouped_response = client.post(card_move_path(project, cycle, card), {"cluster": ""})
    card.refresh_from_db()

    assert first_response.status_code == 302
    assert second_response.status_code == 302
    assert ungrouped_response.status_code == 302
    assert card.cluster is None
    for field, value in original_snapshot.items():
        assert getattr(card, field) == value


def test_invalid_cross_cycle_move_returns_404_without_changing_or_revealing_data(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Hidden Move Project")
    other_project = create_project("Other Move Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Hidden Move Week")
    other_cycle = create_cycle(
        other_project,
        facilitator,
        label="Other Move Week",
    )
    card = create_card(cycle, member, text="Hidden move card")
    other_card = create_card(other_cycle, member, text="Other move card")
    other_cluster = create_cluster(other_cycle, "Other move theme")
    client.force_login(facilitator)

    bad_cluster_response = client.post(
        card_move_path(project, cycle, card),
        {"cluster": str(other_cluster.pk)},
    )
    bad_card_response = client.post(
        card_move_path(project, cycle, other_card),
        {"cluster": ""},
    )
    bad_cycle_response = client.post(
        card_move_path(project, other_cycle, other_card),
        {"cluster": ""},
    )
    bad_project_response = client.post(
        card_move_path(other_project, cycle, card),
        {"cluster": ""},
    )

    for response in [
        bad_cluster_response,
        bad_card_response,
        bad_cycle_response,
        bad_project_response,
    ]:
        content = response.content.decode()
        assert response.status_code == 404
        assert "Hidden Move Project" not in content
        assert "Hidden Move Week" not in content
        assert "Hidden move card" not in content
        assert "Other Move Project" not in content
        assert "Other Move Week" not in content
        assert "Other move card" not in content
        assert "Other move theme" not in content

    card.refresh_from_db()
    other_card.refresh_from_db()
    assert card.cluster is None
    assert other_card.cluster is None


def test_merging_clusters_moves_cards_removes_source_and_keeps_target(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    source = create_cluster(cycle, "Source theme")
    target = create_cluster(cycle, "Target theme")
    source_card = create_card(cycle, member, text="Source card", cluster=source)
    target_card = create_card(cycle, member, text="Target card", cluster=target)
    client.force_login(facilitator)

    response = client.post(
        cluster_merge_path(project, cycle, source),
        {"target_cluster": str(target.pk)},
    )

    assert response.status_code == 302
    assert FeedbackCluster.objects.filter(pk=source.pk).exists() is False
    assert FeedbackCluster.objects.filter(pk=target.pk).exists() is True
    source_card.refresh_from_db()
    target_card.refresh_from_db()
    assert source_card.cluster == target
    assert target_card.cluster == target
    assert FeedbackCard.objects.count() == 2


def test_merging_cluster_into_itself_shows_error_without_changing_assignments(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(cycle)
    card = create_card(cycle, member, cluster=cluster)
    client.force_login(facilitator)

    response = client.post(
        cluster_merge_path(project, cycle, cluster),
        {"target_cluster": str(cluster.pk)},
    )

    assert response.status_code == 200
    assert "Choose a different cluster to merge into." in response.content.decode()
    assert FeedbackCluster.objects.filter(pk=cluster.pk).exists() is True
    card.refresh_from_db()
    assert card.cluster == cluster


def test_splitting_selected_cards_creates_new_cluster_and_moves_only_selected_cards(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    original = create_cluster(cycle, "Mixed theme")
    selected = create_card(cycle, member, text="Selected card", cluster=original)
    unselected = create_card(cycle, member, text="Unselected card", cluster=original)
    client.force_login(facilitator)

    response = client.post(
        cluster_split_path(project, cycle, original),
        {"name": "New theme", "cards": [str(selected.pk)]},
    )

    assert response.status_code == 302
    new_cluster = FeedbackCluster.objects.get(name="New theme")
    selected.refresh_from_db()
    unselected.refresh_from_db()
    assert selected.cluster == new_cluster
    assert unselected.cluster == original


def test_invalid_split_submissions_show_errors_without_creating_cluster(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(cycle)
    card = create_card(cycle, member, cluster=cluster)
    client.force_login(facilitator)

    no_card_response = client.post(
        cluster_split_path(project, cycle, cluster),
        {"name": "Empty split"},
    )
    blank_name_response = client.post(
        cluster_split_path(project, cycle, cluster),
        {"name": "   ", "cards": [str(card.pk)]},
    )

    assert no_card_response.status_code == 200
    assert blank_name_response.status_code == 200
    assert FeedbackCluster.objects.count() == 1
    assert "Select at least one card" in no_card_response.content.decode()
    assert "Cluster name cannot be empty." in blank_name_response.content.decode()
    card.refresh_from_db()
    assert card.cluster == cluster


def test_non_facilitators_cannot_post_to_cluster_endpoints_without_leakage(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    outsider = create_user(username="outsider")
    admin = create_user(username="admin", is_staff=True, is_superuser=True)
    project = create_project("Secret Cluster Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Secret Cluster Week")
    source = create_cluster(cycle, "Secret source")
    target = create_cluster(cycle, "Secret target")
    card = create_card(cycle, member, text="Secret card text", cluster=source)
    endpoints = [
        (cluster_create_path(project, cycle), {"name": "Forbidden"}),
        (cluster_rename_path(project, cycle, source), {"name": "Forbidden"}),
        (card_move_path(project, cycle, card), {"cluster": str(target.pk)}),
        (cluster_merge_path(project, cycle, source), {"target_cluster": str(target.pk)}),
        (
            cluster_split_path(project, cycle, source),
            {"name": "Forbidden", "cards": [str(card.pk)]},
        ),
    ]

    for user in [member, outsider, admin]:
        client.force_login(user)
        for path, data in endpoints:
            response = client.post(path, data)
            content = response.content.decode()
            assert response.status_code == 404
            assert "Secret Cluster Project" not in content
            assert "Secret Cluster Week" not in content
            assert "Secret source" not in content
            assert "Secret target" not in content
            assert "Secret card text" not in content

    assert FeedbackCluster.objects.count() == 2
    card.refresh_from_db()
    assert card.cluster == source


def test_inactive_user_cannot_open_board_or_cluster_endpoints(client):
    inactive = create_user(username="inactive", is_active=False)
    project = create_project()
    add_membership(inactive, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, inactive)
    cluster = create_cluster(cycle)
    card = create_card(cycle, inactive, cluster=cluster)
    board_url = board_path(project, cycle)
    create_url = cluster_create_path(project, cycle)
    client.force_login(inactive)

    board_response = client.get(board_url)
    create_response = client.post(create_url, {"name": "Late"})
    rename_response = client.post(cluster_rename_path(project, cycle, cluster), {"name": "Late"})
    move_response = client.post(card_move_path(project, cycle, card), {"cluster": ""})

    for response, path in [
        (board_response, board_url),
        (create_response, create_url),
        (rename_response, cluster_rename_path(project, cycle, cluster)),
        (move_response, card_move_path(project, cycle, card)),
    ]:
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={path}"


@pytest.mark.parametrize(
    "status",
    [FeedbackCycle.Status.COLLECTING_FEEDBACK, FeedbackCycle.Status.COMPLETED],
)
def test_clustering_is_blocked_outside_retrospective_state(client, status):
    facilitator = create_user(username=f"facilitator-{status}")
    project = create_project(f"Blocked {status}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, status=status)
    cluster = create_cluster(cycle)
    card = create_card(cycle, facilitator, cluster=cluster)
    client.force_login(facilitator)

    responses = [
        client.get(board_path(project, cycle)),
        client.post(cluster_create_path(project, cycle), {"name": "Blocked"}),
        client.post(cluster_rename_path(project, cycle, cluster), {"name": "Blocked"}),
        client.post(card_move_path(project, cycle, card), {"cluster": ""}),
        client.post(cluster_merge_path(project, cycle, cluster), {"target_cluster": str(cluster.pk)}),
        client.post(
            cluster_split_path(project, cycle, cluster),
            {"name": "Blocked", "cards": [str(card.pk)]},
        ),
    ]

    for response in responses:
        assert response.status_code == 404

    cluster.refresh_from_db()
    card.refresh_from_db()
    assert cluster.name == "Release readiness"
    assert card.cluster == cluster


def test_board_preserves_anonymous_and_attributed_labels_in_clusters_and_ungrouped(client):
    facilitator = create_user(
        username="facilitator",
        first_name="Frances",
        last_name="Lead",
        email="facilitator@example.test",
    )
    anonymous_author = create_user(
        username="hidden-author",
        first_name="Hidden",
        last_name="Author",
        email="hidden@example.test",
    )
    project = create_project("Privacy Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(anonymous_author, project)
    cycle = create_cycle(project, facilitator)
    cluster = create_cluster(cycle, "Team rhythm")
    create_card(
        cycle,
        facilitator,
        text="Continue pairing before releases",
        cluster=cluster,
    )
    create_card(
        cycle,
        anonymous_author,
        category=FeedbackCard.Category.STOP,
        text="Stop surprise work",
        is_anonymous=True,
    )
    client.force_login(facilitator)

    response = client.get(board_path(project, cycle))
    content = response.content.decode()

    assert "Continue pairing before releases" in content
    assert "Frances Lead" in content
    assert "Stop surprise work" in content
    assert "Anonymous contributor" in content
    assert "hidden-author" not in content
    assert "Hidden Author" not in content
    assert "hidden@example.test" not in content
    assert "facilitator@example.test" not in content


def test_board_omits_deferred_workflow_behavior_and_preserves_navigation(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    create_card(cycle, member)
    client.force_login(member)

    response = client.get(board_path(project, cycle))
    content = response.content.decode().lower()
    nav = parser_from(response)

    assert set(nav.nav_hrefs) == {reverse("home"), reverse("projects")}
    assert nav.nav_form_actions == [reverse("logout")]
    assert "ai-generated" not in content
    assert "ai suggestion" not in content
    assert "voting" not in content
    assert "vote total" not in content
    assert "discussion notes" not in content
    assert "topic status" not in content
    assert "meeting upload" not in content
    assert "extracted outcomes" not in content
    assert "action item" not in content
    assert "published summaries" not in content
