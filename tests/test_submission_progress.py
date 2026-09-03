from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from projects.models import FeedbackCard, FeedbackCycle, Membership, Project


pytestmark = pytest.mark.django_db


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.form_actions = []
        self._in_nav = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "nav":
            self._in_nav = True
        if not self._in_nav:
            return
        if tag == "a" and "href" in attributes:
            self.hrefs.append(attributes["href"])
        if tag == "form" and "action" in attributes:
            self.form_actions.append(attributes["action"])

    def handle_endtag(self, tag):
        if tag == "nav":
            self._in_nav = False


def create_user(username="member", *, is_active=True, is_staff=False, is_superuser=False):
    return get_user_model().objects.create_user(
        username=username,
        password="UsablePass123!",
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=is_superuser,
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
    text="Private feedback text",
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


def progress_rows(response):
    return response.context["team_submission_progress"]


def navigation_from(response):
    parser = NavigationParser()
    parser.feed(response.content.decode())
    return parser


def test_facilitator_dashboard_shows_submission_progress_for_collecting_cycle(client):
    facilitator = create_user(username="carla-facilitator")
    member = create_user(username="marta-member")
    second_facilitator = create_user(username="zane-facilitator")
    project = create_project("Platform Retrospective")
    add_membership(second_facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    cycle = create_cycle(project, facilitator, label="Week 34")
    create_card(cycle, facilitator, text="My private facilitator card")
    create_card(cycle, second_facilitator, text="Second facilitator private card")
    client.force_login(facilitator)

    response = client.get(dashboard_path(project))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Team submission progress" in content
    assert progress_rows(response) == [
        {"user_label": "carla-facilitator", "has_submitted_feedback": True},
        {"user_label": "marta-member", "has_submitted_feedback": False},
        {"user_label": "zane-facilitator", "has_submitted_feedback": True},
    ]
    assert content.index("carla-facilitator") < content.index("marta-member")
    assert content.index("marta-member") < content.index("zane-facilitator")
    assert "Submitted" in content
    assert "Not submitted" in content


def test_submission_progress_counts_only_cards_for_the_active_collecting_cycle(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Scoped Retrospective")
    other_project = create_project("Other Retrospective")
    retrospective_project = create_project("Retrospective State")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    active_cycle = create_cycle(project, facilitator, label="Current Week")
    completed_cycle = create_cycle(
        project,
        facilitator,
        label="Completed Week",
        status=FeedbackCycle.Status.COMPLETED,
    )
    other_collecting_cycle = create_cycle(other_project, facilitator, label="Other Week")
    other_retrospective_cycle = create_cycle(
        retrospective_project,
        facilitator,
        label="Other Retrospective Week",
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    create_card(completed_cycle, member, text="Completed cycle card")
    create_card(other_collecting_cycle, member, text="Wrong project card")
    create_card(other_retrospective_cycle, member, text="Non-collecting cycle card")
    client.force_login(facilitator)

    response = client.get(dashboard_path(project))

    assert progress_rows(response) == [
        {"user_label": "facilitator", "has_submitted_feedback": False},
        {"user_label": "member", "has_submitted_feedback": False},
    ]

    create_card(active_cycle, member, text="Current cycle card")

    response = client.get(dashboard_path(project))

    assert progress_rows(response) == [
        {"user_label": "facilitator", "has_submitted_feedback": False},
        {"user_label": "member", "has_submitted_feedback": True},
    ]


def test_anonymous_cards_count_without_exposing_card_details(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Private Progress Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Week 34")
    card = create_card(
        cycle,
        member,
        category=FeedbackCard.Category.STOP,
        text="Sensitive unrevealed card text",
        is_anonymous=True,
    )
    client.force_login(facilitator)

    response = client.get(dashboard_path(project))
    content = response.content.decode()

    assert {"user_label": "member", "has_submitted_feedback": True} in progress_rows(
        response
    )
    assert "Sensitive unrevealed card text" not in content
    assert "Stop" not in content
    assert "Anonymous" not in content
    assert "anonymous" not in content
    assert "card count" not in content.lower()
    assert str(card.pk) not in [row.get("card_id") for row in progress_rows(response)]
    assert set(progress_rows(response)[0]) == {"user_label", "has_submitted_feedback"}


def test_progress_is_hidden_without_a_collecting_cycle(client):
    facilitator = create_user(username="facilitator")
    no_cycle_project = create_project("No Cycle")
    retrospective_project = create_project("Retrospective Cycle")
    add_membership(facilitator, no_cycle_project, Membership.Role.FACILITATOR)
    add_membership(facilitator, retrospective_project, Membership.Role.FACILITATOR)
    retrospective_cycle = create_cycle(
        retrospective_project,
        facilitator,
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    client.force_login(facilitator)

    no_cycle_response = client.get(dashboard_path(no_cycle_project))
    retrospective_response = client.get(dashboard_path(retrospective_project))
    retrospective_content = retrospective_response.content.decode()

    assert "Team submission progress" not in no_cycle_response.content.decode()
    assert no_cycle_response.context["team_submission_progress"] is None
    assert "Team submission progress" not in retrospective_content
    assert retrospective_response.context["team_submission_progress"] is None
    assert feedback_path(retrospective_project, retrospective_cycle) not in retrospective_content


def test_team_member_dashboard_hides_team_progress_but_preserves_own_submission(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    other_member = create_user(username="other-member")
    project = create_project("Member Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    add_membership(other_member, project)
    cycle = create_cycle(project, facilitator, label="Week 34")
    create_card(cycle, member, text="Member own card")
    create_card(cycle, other_member, text="Other member hidden card")
    client.force_login(member)

    response = client.get(dashboard_path(project))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Submitted for Week 34." in content
    assert feedback_path(project, cycle) in content
    assert "Open feedback form" in content
    assert "Team submission progress" not in content
    assert response.context["team_submission_progress"] is None
    assert "other-member" not in content
    assert "Other member hidden card" not in content


def test_project_dashboard_denies_non_member_without_revealing_progress_data(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    outsider = create_user(username="outsider")
    project = create_project("Confidential Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Confidential Week")
    create_card(cycle, member, text="Hidden unrevealed card")
    client.force_login(outsider)

    response = client.get(dashboard_path(project))
    content = response.content.decode()

    assert response.status_code == 404
    assert "Confidential Retrospective" not in content
    assert "Confidential Week" not in content
    assert "member" not in content
    assert "Hidden unrevealed card" not in content
    assert "Team submission progress" not in content


def test_inactive_user_cannot_access_dashboard_progress_through_membership(client):
    inactive_facilitator = create_user(username="inactive", is_active=False)
    project = create_project("Inactive Retrospective")
    add_membership(inactive_facilitator, project, Membership.Role.FACILITATOR)
    create_cycle(project, inactive_facilitator)
    path = dashboard_path(project)
    client.force_login(inactive_facilitator)

    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={path}"


def test_staff_or_superuser_without_membership_cannot_access_dashboard_progress(client):
    admin = create_user(username="admin", is_staff=True, is_superuser=True)
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Admin Hidden Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Admin Hidden Week")
    create_card(cycle, member, text="Hidden from admin")
    client.force_login(admin)

    response = client.get(dashboard_path(project))
    content = response.content.decode()

    assert response.status_code == 404
    assert "Admin Hidden Retrospective" not in content
    assert "Admin Hidden Week" not in content
    assert "Hidden from admin" not in content
    assert "Team submission progress" not in content


def test_feedback_submission_page_remains_private_to_signed_in_contributor(client):
    facilitator = create_user(username="facilitator")
    member = create_user(username="member")
    project = create_project("Feedback Privacy Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Week 34")
    create_card(cycle, facilitator, text="Facilitator own card")
    create_card(cycle, member, text="Member private card")
    client.force_login(facilitator)

    response = client.get(feedback_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Facilitator own card" in content
    assert "Member private card" not in content
    assert "member" not in content


def test_submission_progress_preserves_top_level_navigation_scope(client):
    facilitator = create_user(username="facilitator")
    project = create_project("Navigation Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    create_cycle(project, facilitator)
    client.force_login(facilitator)

    response = client.get(dashboard_path(project))
    nav = navigation_from(response)
    content = response.content.decode().lower()

    assert set(nav.hrefs) == {reverse("home"), reverse("projects")}
    assert nav.form_actions == [reverse("logout")]
    assert "board" not in content
    assert "reveal" not in content
    assert "clustering" not in content
    assert "voting" not in content
    assert "discussion" not in content
    assert "meeting upload" not in content
    assert "extraction" not in content
    assert "summary" not in content
