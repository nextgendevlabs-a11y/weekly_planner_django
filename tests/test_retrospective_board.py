from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from projects.models import FeedbackCard, FeedbackCycle, Membership, Project


pytestmark = pytest.mark.django_db


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.forms = []
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
    status=FeedbackCycle.Status.COLLECTING_FEEDBACK,
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
):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=category,
        text=text,
        is_anonymous=is_anonymous,
    )


def dashboard_path(project):
    return reverse("project_dashboard", kwargs={"project_id": project.pk})


def feedback_path(project, cycle):
    return reverse(
        "feedback_submission",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def reveal_path(project, cycle):
    return reverse(
        "feedback_cycle_reveal",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def board_path(project, cycle):
    return reverse(
        "retrospective_board",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def parser_from(response):
    parser = PageParser()
    parser.feed(response.content.decode())
    return parser


def test_dashboard_reveal_and_board_entry_states_for_facilitators_and_members(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Dashboard Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)

    client.force_login(facilitator)
    empty_response = client.get(dashboard_path(project))
    empty_content = empty_response.content.decode()

    assert "No feedback cycle has been started for this project yet." in empty_content
    assert "Reveal feedback" not in empty_content
    assert "Open retrospective board" not in empty_content

    cycle = create_cycle(project, facilitator, label="Week 34")
    reveal_url = reveal_path(project, cycle)
    board_url = board_path(project, cycle)

    facilitator_response = client.get(dashboard_path(project))
    facilitator_content = facilitator_response.content.decode()
    facilitator_parser = parser_from(facilitator_response)

    assert "Collecting feedback" in facilitator_content
    assert "Reveal feedback" in facilitator_content
    assert {"action": reveal_url, "method": "post"} in facilitator_parser.forms
    assert board_url not in facilitator_parser.hrefs

    client.force_login(member)
    member_response = client.get(dashboard_path(project))
    member_content = member_response.content.decode()
    member_parser = parser_from(member_response)

    assert "Collecting feedback" in member_content
    assert "Reveal feedback" not in member_content
    assert reveal_url not in [form["action"] for form in member_parser.forms]
    assert board_url not in member_parser.hrefs

    cycle.status = FeedbackCycle.Status.RETROSPECTIVE
    cycle.save(update_fields=["status"])

    revealed_response = client.get(dashboard_path(project))
    revealed_content = revealed_response.content.decode()
    revealed_parser = parser_from(revealed_response)

    assert "Week 34" in revealed_content
    assert "Retrospective" in revealed_content
    assert "Open retrospective board" in revealed_content
    assert board_url in revealed_parser.hrefs
    assert feedback_path(project, cycle) not in revealed_content
    assert "Team submission progress" not in revealed_content
    assert "Reveal feedback" not in revealed_content


def test_reveal_redirects_anonymous_visitors_with_next(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    path = reveal_path(project, cycle)

    response = client.post(path)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={path}"


def test_reveal_is_post_only_and_csrf_protected(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator)
    path = reveal_path(project, cycle)
    client.force_login(facilitator)

    get_response = client.get(path)

    assert get_response.status_code == 405

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(facilitator)

    post_response = csrf_client.post(path)

    assert post_response.status_code == 403
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK


def test_facilitator_reveal_transitions_cycle_and_preserves_cards(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project()
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    attributed_card = create_card(
        cycle,
        facilitator,
        text="Start reviewing handoffs together",
    )
    anonymous_card = create_card(
        cycle,
        member,
        category=FeedbackCard.Category.STOP,
        text="Stop surprise scope changes",
        is_anonymous=True,
    )
    card_snapshots = {
        card.pk: {
            "text": card.text,
            "category": card.category,
            "author": card.author,
            "is_anonymous": card.is_anonymous,
            "created_at": card.created_at,
            "updated_at": card.updated_at,
        }
        for card in [attributed_card, anonymous_card]
    }
    client.force_login(facilitator)

    response = client.post(reveal_path(project, cycle))

    assert response.status_code == 302
    assert response["Location"] == board_path(project, cycle)
    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.RETROSPECTIVE

    for card in [attributed_card, anonymous_card]:
        card.refresh_from_db()
        assert card.text == card_snapshots[card.pk]["text"]
        assert card.category == card_snapshots[card.pk]["category"]
        assert card.author == card_snapshots[card.pk]["author"]
        assert card.is_anonymous == card_snapshots[card.pk]["is_anonymous"]
        assert card.created_at == card_snapshots[card.pk]["created_at"]
        assert card.updated_at == card_snapshots[card.pk]["updated_at"]


def test_collecting_cycle_can_be_revealed_without_cards(client):
    facilitator = create_user(username="facilitator")
    project = create_project("Empty Board Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Empty Week")
    client.force_login(facilitator)

    response = client.post(reveal_path(project, cycle))
    board_response = client.get(response["Location"])
    content = board_response.content.decode()

    assert response.status_code == 302
    assert board_response.status_code == 200
    assert "No Start feedback was submitted." in content
    assert "No Stop feedback was submitted." in content
    assert "No Continue feedback was submitted." in content


def test_reveal_denies_team_members_non_members_staff_and_mismatched_cycles(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    outsider = create_user(username="outsider")
    admin = create_user(username="admin", is_staff=True, is_superuser=True)
    project = create_project("Hidden Reveal Retrospective")
    other_project = create_project("Other Reveal Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(facilitator, other_project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Hidden Reveal Week")
    other_cycle = create_cycle(other_project, facilitator, label="Other Reveal Week")
    create_card(cycle, member, text="Private reveal card")
    create_card(other_cycle, facilitator, text="Other project card")

    client.force_login(member)
    member_response = client.post(reveal_path(project, cycle))
    client.force_login(outsider)
    outsider_response = client.post(reveal_path(project, cycle))
    client.force_login(admin)
    admin_response = client.post(reveal_path(project, cycle))
    client.force_login(facilitator)
    mismatch_response = client.post(reveal_path(project, other_cycle))

    for response in [
        member_response,
        outsider_response,
        admin_response,
        mismatch_response,
    ]:
        content = response.content.decode()
        assert response.status_code == 404
        assert "Hidden Reveal Retrospective" not in content
        assert "Hidden Reveal Week" not in content
        assert "Private reveal card" not in content
        assert "Other Reveal Retrospective" not in content
        assert "Other Reveal Week" not in content
        assert "Other project card" not in content

    cycle.refresh_from_db()
    assert cycle.status == FeedbackCycle.Status.COLLECTING_FEEDBACK


def test_inactive_user_cannot_access_reveal_or_board_through_membership(client):
    inactive_user = create_user(username="inactive", is_active=False)
    project = create_project("Inactive Retrospective")
    add_membership(inactive_user, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(
        project,
        inactive_user,
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    reveal_url = reveal_path(project, cycle)
    board_url = board_path(project, cycle)
    client.force_login(inactive_user)

    reveal_response = client.post(reveal_url)
    board_response = client.get(board_url)

    assert reveal_response.status_code == 302
    assert reveal_response["Location"] == f"{reverse('login')}?next={reveal_url}"
    assert board_response.status_code == 302
    assert board_response["Location"] == f"{reverse('login')}?next={board_url}"


@pytest.mark.parametrize(
    "status",
    [FeedbackCycle.Status.RETROSPECTIVE, FeedbackCycle.Status.COMPLETED],
)
def test_cycle_cannot_be_revealed_again_after_collection_ends(client, status):
    facilitator = create_user(username=f"facilitator-{status}")
    project = create_project(f"Repeat Reveal {status}")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, status=status)
    client.force_login(facilitator)

    response = client.post(reveal_path(project, cycle))

    assert response.status_code == 404
    cycle.refresh_from_db()
    assert cycle.status == status


def test_feedback_submission_endpoints_are_closed_after_reveal(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(
        project,
        facilitator,
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    card = create_card(cycle, member, text="Existing revealed note")
    client.force_login(member)

    page_response = client.get(feedback_path(project, cycle))
    create_response = client.post(
        reverse(
            "feedback_card_create",
            kwargs={
                "project_id": project.pk,
                "cycle_id": cycle.pk,
                "category": FeedbackCard.Category.START,
            },
        ),
        {"text": "Late card"},
    )
    edit_response = client.post(
        reverse(
            "feedback_card_update",
            kwargs={
                "project_id": project.pk,
                "cycle_id": cycle.pk,
                "card_id": card.pk,
            },
        ),
        {"text": "Late edit"},
    )
    delete_response = client.post(
        reverse(
            "feedback_card_delete",
            kwargs={
                "project_id": project.pk,
                "cycle_id": cycle.pk,
                "card_id": card.pk,
            },
        )
    )

    assert page_response.status_code == 404
    assert create_response.status_code == 404
    assert edit_response.status_code == 404
    assert delete_response.status_code == 404
    assert FeedbackCard.objects.count() == 1
    card.refresh_from_db()
    assert card.text == "Existing revealed note"


def test_board_redirects_anonymous_visitors_with_next(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(
        project,
        facilitator,
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    path = board_path(project, cycle)

    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={path}"


@pytest.mark.parametrize(
    "role",
    [Membership.Role.TEAM_MEMBER, Membership.Role.FACILITATOR],
)
def test_board_is_visible_to_project_members_after_reveal(client, role):
    user = create_user(username=f"viewer-{role}")
    facilitator = create_user(username="facilitator")
    project = create_project("Platform Retrospective")
    add_membership(user, project, role)
    cycle = create_cycle(
        project,
        facilitator,
        label="Week 34",
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    client.force_login(user)

    response = client.get(board_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "projects/retrospective_board.html" in [
        template.name for template in response.templates
    ]
    assert "base.html" in [template.name for template in response.templates]
    assert "Platform Retrospective" in content
    assert "Week 34" in content
    assert ">Start<" in content
    assert ">Stop<" in content
    assert ">Continue<" in content


def test_board_denies_non_members_staff_mismatched_projects_and_collecting_cycles(client):
    member = create_user(username="member")
    outsider = create_user(username="outsider")
    admin = create_user(username="admin", is_staff=True, is_superuser=True)
    facilitator = create_user(username="facilitator")
    project = create_project("Hidden Board Retrospective")
    other_project = create_project("Other Board Retrospective")
    add_membership(member, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Hidden Board Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    collecting_cycle = create_cycle(
        other_project,
        facilitator,
        label="Collecting Board Week",
    )
    create_card(cycle, member, text="Hidden board card")
    create_card(collecting_cycle, facilitator, text="Collecting card")

    client.force_login(outsider)
    outsider_response = client.get(board_path(project, cycle))
    client.force_login(admin)
    admin_response = client.get(board_path(project, cycle))
    client.force_login(member)
    mismatch_response = client.get(board_path(project, collecting_cycle))
    collecting_response = client.get(board_path(other_project, collecting_cycle))

    for response in [
        outsider_response,
        admin_response,
        mismatch_response,
        collecting_response,
    ]:
        content = response.content.decode()
        assert response.status_code == 404
        assert "Hidden Board Retrospective" not in content
        assert "Hidden Board Week" not in content
        assert "Hidden board card" not in content
        assert "Other Board Retrospective" not in content
        assert "Collecting Board Week" not in content
        assert "Collecting card" not in content


def test_board_shows_all_revealed_cards_grouped_and_preserves_anonymity(client):
    facilitator = create_user(
        username="facilitator",
        first_name="Frances",
        last_name="Lead",
        email="facilitator@example.test",
    )
    member = create_user(username="member")
    anonymous_author = create_user(
        username="secret-author",
        first_name="Hidden",
        last_name="Person",
        email="secret@example.test",
    )
    project = create_project("Grouped Retrospective")
    other_project = create_project("Other Project")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(anonymous_author, project)
    cycle = create_cycle(
        project,
        facilitator,
        label="Week 34",
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    completed_cycle = create_cycle(
        project,
        facilitator,
        label="Old Week",
        status=FeedbackCycle.Status.COMPLETED,
    )
    other_cycle = create_cycle(
        other_project,
        facilitator,
        label="Other Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    create_card(
        cycle,
        facilitator,
        category=FeedbackCard.Category.START,
        text="Start pairing on risky releases",
    )
    create_card(
        cycle,
        anonymous_author,
        category=FeedbackCard.Category.STOP,
        text="Stop changing priorities after planning",
        is_anonymous=True,
    )
    create_card(
        cycle,
        member,
        category=FeedbackCard.Category.CONTINUE,
        text="Continue sharing customer notes",
    )
    create_card(completed_cycle, member, text="Old cycle card")
    create_card(other_cycle, member, text="Other project card")
    client.force_login(member)

    response = client.get(board_path(project, cycle))
    content = response.content.decode()
    sections = {
        section["value"]: section["cards"]
        for section in response.context["category_sections"]
    }

    assert content.count("Start pairing on risky releases") == 1
    assert content.count("Stop changing priorities after planning") == 1
    assert content.count("Continue sharing customer notes") == 1
    assert "Old cycle card" not in content
    assert "Other project card" not in content

    assert sections[FeedbackCard.Category.START] == [
        {
            "text": "Start pairing on risky releases",
            "is_anonymous": False,
            "author_label": "Frances Lead",
        }
    ]
    assert sections[FeedbackCard.Category.STOP] == [
        {
            "text": "Stop changing priorities after planning",
            "is_anonymous": True,
            "author_label": "Anonymous contributor",
        }
    ]
    assert sections[FeedbackCard.Category.CONTINUE] == [
        {
            "text": "Continue sharing customer notes",
            "is_anonymous": False,
            "author_label": "member",
        }
    ]
    assert "Anonymous contributor" in content
    assert "secret-author" not in content
    assert "Hidden Person" not in content
    assert "secret@example.test" not in content
    assert set(sections[FeedbackCard.Category.STOP][0]) == {
        "text",
        "is_anonymous",
        "author_label",
    }
    assert anonymous_author.pk not in sections[FeedbackCard.Category.STOP][0].values()
    assert str(anonymous_author.pk) not in sections[FeedbackCard.Category.STOP][0].values()
    assert "facilitator@example.test" not in content
    assert "Save changes" not in content
    assert "Delete" not in content
    assert "Add Start card" not in content
    assert "Team submission progress" not in content
    assert "Reveal feedback" not in content
    assert "clustering" not in content.lower()
    assert "voting" not in content.lower()
    assert "discussion" not in content.lower()
    assert "meeting upload" not in content.lower()
    assert "extracted outcomes" not in content.lower()
    assert "action items" not in content.lower()
    assert "published summaries" not in content.lower()


def test_board_navigation_lists_only_top_level_destinations(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(
        project,
        facilitator,
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    client.force_login(member)

    response = client.get(board_path(project, cycle))
    nav = parser_from(response)

    assert set(nav.nav_hrefs) == {reverse("home"), reverse("projects")}
    assert nav.nav_form_actions == [reverse("logout")]
