from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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
    text="Improve release notes",
    is_anonymous=False,
):
    return FeedbackCard.objects.create(
        cycle=cycle,
        author=author,
        category=category,
        text=text,
        is_anonymous=is_anonymous,
    )


def feedback_path(project, cycle):
    return reverse(
        "feedback_submission",
        kwargs={"project_id": project.pk, "cycle_id": cycle.pk},
    )


def create_card_path(project, cycle, category):
    return reverse(
        "feedback_card_create",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "category": category,
        },
    )


def edit_card_path(project, cycle, card):
    return reverse(
        "feedback_card_update",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "card_id": card.pk,
        },
    )


def delete_card_path(project, cycle, card):
    return reverse(
        "feedback_card_delete",
        kwargs={
            "project_id": project.pk,
            "cycle_id": cycle.pk,
            "card_id": card.pk,
        },
    )


def navigation_from(response):
    parser = NavigationParser()
    parser.feed(response.content.decode())
    return parser


def test_feedback_card_has_expected_fields_defaults_and_relationships():
    author = create_user()
    facilitator = create_user(username="facilitator")
    project = create_project("Platform Retrospective")
    cycle = create_cycle(project, facilitator)

    card = create_card(cycle, author)

    assert card.cycle == cycle
    assert card.author == author
    assert card.category == FeedbackCard.Category.START
    assert card.text == "Improve release notes"
    assert card.is_anonymous is False
    assert card.created_at is not None
    assert card.updated_at is not None
    assert str(card) == (
        "Start feedback by member for Week 34 Retrospective "
        "(Platform Retrospective)"
    )


def test_feedback_card_category_is_limited_to_start_stop_continue():
    author = create_user()
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    card = FeedbackCard(
        cycle=cycle,
        author=author,
        category="other",
        text="A useful thought",
    )

    with pytest.raises(ValidationError):
        card.full_clean()

    with pytest.raises(IntegrityError), transaction.atomic():
        create_card(cycle, author, category="other")


def test_feedback_card_rejects_empty_or_whitespace_text():
    author = create_user()
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    card = FeedbackCard(
        cycle=cycle,
        author=author,
        category=FeedbackCard.Category.CONTINUE,
        text="   ",
    )

    with pytest.raises(ValidationError) as error:
        card.full_clean()

    assert "text" in error.value.message_dict
    assert "Feedback text cannot be empty." in error.value.message_dict["text"]


def test_deleting_feedback_cycle_removes_feedback_cards():
    author = create_user()
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    create_card(cycle, author)

    cycle.delete()

    assert FeedbackCard.objects.count() == 0


def test_feedback_submission_redirects_anonymous_visitors_with_next(client):
    facilitator = create_user(username="facilitator")
    project = create_project()
    cycle = create_cycle(project, facilitator)
    path = feedback_path(project, cycle)

    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={path}"


@pytest.mark.parametrize(
    "role",
    [Membership.Role.TEAM_MEMBER, Membership.Role.FACILITATOR],
)
def test_feedback_submission_page_is_visible_to_project_members(client, role):
    user = create_user(username=f"user-{role}")
    facilitator = create_user(username="facilitator")
    project = create_project("Platform Retrospective")
    add_membership(user, project, role)
    cycle = create_cycle(project, facilitator, label="Week 34")
    client.force_login(user)

    response = client.get(feedback_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "projects/feedback_submission.html" in [
        template.name for template in response.templates
    ]
    assert "base.html" in [template.name for template in response.templates]
    assert "Platform Retrospective" in content
    assert "Week 34" in content
    assert ">Start<" in content
    assert ">Stop<" in content
    assert ">Continue<" in content
    assert "Add Start card" in content
    assert "Add Stop card" in content
    assert "Add Continue card" in content
    assert content.count('name="is_anonymous"') == 3


def test_feedback_submission_page_shows_only_the_signed_in_users_cards(client):
    facilitator = create_user(username="facilitator")
    other_member = create_user(username="other-member")
    project = create_project("Private Retrospective")
    add_membership(facilitator, project, Membership.Role.FACILITATOR)
    add_membership(other_member, project)
    cycle = create_cycle(project, facilitator)
    create_card(cycle, facilitator, text="My own facilitator note")
    create_card(cycle, other_member, text="Someone else's private note")
    client.force_login(facilitator)

    response = client.get(feedback_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 200
    assert "My own facilitator note" in content
    assert "Someone else&#x27;s private note" not in content
    assert "other-member" not in content


def test_feedback_submission_returns_404_for_non_member_without_revealing_data(client):
    member = create_user(username="member")
    outsider = create_user(username="outsider")
    facilitator = create_user(username="facilitator")
    project = create_project("Confidential Retrospective")
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Confidential Week")
    create_card(cycle, member, text="Private unrevealed feedback")
    client.force_login(outsider)

    response = client.get(feedback_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 404
    assert "Confidential Retrospective" not in content
    assert "Confidential Week" not in content
    assert "Private unrevealed feedback" not in content


def test_feedback_submission_returns_404_when_cycle_does_not_belong_to_project(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project("Member Retrospective")
    other_project = create_project("Other Retrospective")
    add_membership(member, project)
    add_membership(member, other_project)
    cycle = create_cycle(other_project, facilitator, label="Other Week")
    client.force_login(member)

    response = client.get(feedback_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 404
    assert "Other Week" not in content


def test_inactive_user_cannot_open_feedback_submission_through_membership(client):
    inactive_user = create_user(username="inactive", is_active=False)
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(inactive_user, project)
    cycle = create_cycle(project, facilitator)
    path = feedback_path(project, cycle)
    client.force_login(inactive_user)

    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('login')}?next={path}"


def test_staff_or_superuser_without_membership_cannot_open_feedback_submission(client):
    admin = create_user(username="admin", is_staff=True, is_superuser=True)
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project("Admin Hidden Retrospective")
    add_membership(member, project)
    cycle = create_cycle(project, facilitator, label="Admin Hidden Week")
    create_card(cycle, member, text="Hidden from non-members")
    client.force_login(admin)

    response = client.get(feedback_path(project, cycle))
    content = response.content.decode()

    assert response.status_code == 404
    assert "Admin Hidden Retrospective" not in content
    assert "Admin Hidden Week" not in content
    assert "Hidden from non-members" not in content


def test_valid_feedback_card_submission_records_requested_cycle_author_category_and_anonymity(
    client,
):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    other_author = create_user(username="other-author")
    project = create_project()
    other_project = create_project("Other Retrospective")
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    other_cycle = create_cycle(other_project, facilitator)
    client.force_login(member)

    response = client.post(
        create_card_path(project, cycle, FeedbackCard.Category.STOP),
        {
            "text": "Stop changing priorities mid-cycle",
            "is_anonymous": "on",
            "project": str(other_project.pk),
            "cycle": str(other_cycle.pk),
            "author": str(other_author.pk),
            "category": FeedbackCard.Category.CONTINUE,
        },
    )

    assert response.status_code == 302
    assert response["Location"] == feedback_path(project, cycle)
    card = FeedbackCard.objects.get()
    assert card.cycle == cycle
    assert card.cycle.project == project
    assert card.author == member
    assert card.category == FeedbackCard.Category.STOP
    assert card.text == "Stop changing priorities mid-cycle"
    assert card.is_anonymous is True


def test_feedback_card_submission_defaults_to_attributed(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    client.force_login(member)

    client.post(
        create_card_path(project, cycle, FeedbackCard.Category.START),
        {"text": "Start pairing on risky changes"},
    )

    assert FeedbackCard.objects.get().is_anonymous is False


def test_invalid_feedback_card_submission_shows_errors_without_creating_card(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    client.force_login(member)

    response = client.post(
        create_card_path(project, cycle, FeedbackCard.Category.CONTINUE),
        {"text": "   "},
    )

    assert response.status_code == 200
    assert FeedbackCard.objects.count() == 0
    assert "Feedback text cannot be empty." in response.content.decode()
    assert "errorlist" in response.content.decode()


def test_unsupported_feedback_category_does_not_create_card(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    client.force_login(member)

    response = client.post(
        create_card_path(project, cycle, "other"),
        {"text": "This category should not be accepted"},
    )

    assert response.status_code == 404
    assert FeedbackCard.objects.count() == 0


def test_editing_own_feedback_card_changes_text_and_anonymous_value(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    other_author = create_user(username="other-author")
    project = create_project()
    other_project = create_project("Other Retrospective")
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    other_cycle = create_cycle(other_project, facilitator)
    card = create_card(cycle, member, category=FeedbackCard.Category.START)
    client.force_login(member)

    response = client.post(
        edit_card_path(project, cycle, card),
        {
            "text": "Start sharing context before planning",
            "is_anonymous": "on",
            "project": str(other_project.pk),
            "cycle": str(other_cycle.pk),
            "author": str(other_author.pk),
            "category": FeedbackCard.Category.STOP,
        },
    )

    assert response.status_code == 302
    card.refresh_from_db()
    assert card.text == "Start sharing context before planning"
    assert card.is_anonymous is True
    assert card.cycle == cycle
    assert card.author == member
    assert card.category == FeedbackCard.Category.START


def test_invalid_feedback_card_edit_shows_errors_without_updating_card(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, member, text="Original note")
    client.force_login(member)

    response = client.post(edit_card_path(project, cycle, card), {"text": "   "})

    assert response.status_code == 200
    card.refresh_from_db()
    assert card.text == "Original note"
    assert "Feedback text cannot be empty." in response.content.decode()


def test_deleting_own_feedback_card_removes_it_from_submission_page(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, member, text="Remove this note")
    client.force_login(member)

    response = client.post(delete_card_path(project, cycle, card))

    assert response.status_code == 302
    assert FeedbackCard.objects.filter(pk=card.pk).exists() is False
    page_response = client.get(feedback_path(project, cycle))
    assert "Remove this note" not in page_response.content.decode()


def test_non_owners_cannot_edit_or_delete_feedback_cards_without_revealing_text(client):
    owner = create_user(username="owner")
    other_member = create_user(username="other-member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(owner, project)
    add_membership(other_member, project)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, owner, text="Owner private feedback")
    client.force_login(other_member)

    edit_response = client.post(
        edit_card_path(project, cycle, card),
        {"text": "Trying to overwrite"},
    )
    delete_response = client.post(delete_card_path(project, cycle, card))

    assert edit_response.status_code == 404
    assert delete_response.status_code == 404
    assert "Owner private feedback" not in edit_response.content.decode()
    assert "Owner private feedback" not in delete_response.content.decode()
    card.refresh_from_db()
    assert card.text == "Owner private feedback"


def test_non_owner_get_requests_to_card_endpoints_do_not_reveal_text(client):
    owner = create_user(username="owner")
    other_member = create_user(username="other-member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(owner, project)
    add_membership(other_member, project)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, owner, text="Owner private feedback")
    client.force_login(other_member)

    edit_response = client.get(edit_card_path(project, cycle, card))
    delete_response = client.get(delete_card_path(project, cycle, card))

    assert edit_response.status_code == 404
    assert delete_response.status_code == 404
    assert "Owner private feedback" not in edit_response.content.decode()
    assert "Owner private feedback" not in delete_response.content.decode()


def test_feedback_card_changes_are_blocked_when_cycle_is_not_collecting(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    card = create_card(cycle, member, text="Existing private note")
    cycle.status = FeedbackCycle.Status.RETROSPECTIVE
    cycle.save()
    client.force_login(member)

    create_response = client.post(
        create_card_path(project, cycle, FeedbackCard.Category.START),
        {"text": "Late feedback"},
    )
    edit_response = client.post(
        edit_card_path(project, cycle, card),
        {"text": "Late edit"},
    )
    delete_response = client.post(delete_card_path(project, cycle, card))

    assert create_response.status_code == 404
    assert edit_response.status_code == 404
    assert delete_response.status_code == 404
    assert FeedbackCard.objects.count() == 1
    card.refresh_from_db()
    assert card.text == "Existing private note"


def test_project_dashboard_links_to_feedback_form_and_shows_own_submission_state(client):
    member = create_user(username="member")
    other_member = create_user(username="other-member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    add_membership(other_member, project)
    cycle = create_cycle(project, facilitator, label="Week 34")
    feedback_url = feedback_path(project, cycle)
    client.force_login(member)

    not_submitted_response = client.get(
        reverse("project_dashboard", kwargs={"project_id": project.pk})
    )
    not_submitted_content = not_submitted_response.content.decode()

    assert "Not submitted yet for Week 34." in not_submitted_content
    assert feedback_url in not_submitted_content
    assert "Open feedback form" in not_submitted_content

    create_card(cycle, other_member, text="Other member private note")
    other_only_response = client.get(
        reverse("project_dashboard", kwargs={"project_id": project.pk})
    )

    assert "Not submitted yet for Week 34." in other_only_response.content.decode()

    create_card(cycle, member, text="Member private note")
    submitted_response = client.get(
        reverse("project_dashboard", kwargs={"project_id": project.pk})
    )
    submitted_content = submitted_response.content.decode()

    assert "Submitted for Week 34." in submitted_content
    assert "Other member private note" not in submitted_content
    assert "other-member" not in submitted_content
    assert "team-wide" not in submitted_content.lower()
    assert "progress" not in submitted_content.lower()


def test_project_dashboard_does_not_link_to_feedback_form_without_collecting_cycle(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project_without_cycle = create_project("No Cycle")
    project_with_retrospective_cycle = create_project("Retrospective Cycle")
    add_membership(member, project_without_cycle)
    add_membership(member, project_with_retrospective_cycle)
    retrospective_cycle = create_cycle(
        project_with_retrospective_cycle,
        facilitator,
        status=FeedbackCycle.Status.RETROSPECTIVE,
    )
    client.force_login(member)

    no_cycle_response = client.get(
        reverse("project_dashboard", kwargs={"project_id": project_without_cycle.pk})
    )
    retrospective_response = client.get(
        reverse(
            "project_dashboard",
            kwargs={"project_id": project_with_retrospective_cycle.pk},
        )
    )

    assert "There is no open Start, Stop, and Continue submission yet." in (
        no_cycle_response.content.decode()
    )
    assert "There is no open Start, Stop, and Continue submission yet." in (
        retrospective_response.content.decode()
    )
    assert reverse(
        "feedback_submission",
        kwargs={
            "project_id": project_with_retrospective_cycle.pk,
            "cycle_id": retrospective_cycle.pk,
        },
    ) not in retrospective_response.content.decode()


def test_feedback_submission_navigation_lists_only_top_level_destinations(client):
    member = create_user(username="member")
    facilitator = create_user(username="facilitator")
    project = create_project()
    add_membership(member, project)
    cycle = create_cycle(project, facilitator)
    client.force_login(member)

    response = client.get(feedback_path(project, cycle))
    nav = navigation_from(response)

    assert set(nav.hrefs) == {reverse("home"), reverse("projects")}
    assert nav.form_actions == [reverse("logout")]
